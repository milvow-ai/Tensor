"""Renewal reminders and human task alerts.

Monitors connection plans for upcoming billing dates:
- Emits a reminder 3 days before billing_day/renews_on with usage % and keep/cancel suggestion.
- Ensures reminders fire exactly once per billing cycle (deduplicated by ref).
- Generates human_task alerts for upgrades and cancellations with the provider's billing URL
  (never automates payments).
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import structlog

from farm.db.pool import DbPool
from farm.manager.alerts import create_alert

log = structlog.get_logger(__name__)


def compute_next_renewal_date(plan: dict[str, Any], now: datetime | None = None) -> date | None:
    """Calculate the next renewal date from plan['renews_on'] or plan['billing_day']."""
    current_dt = now or datetime.now(UTC)
    current_date = current_dt.date()

    if plan.get("renews_on"):
        val = plan["renews_on"]
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            try:
                return date.fromisoformat(val[:10])
            except ValueError:
                pass

    billing_day = plan.get("billing_day")
    if billing_day is not None:
        try:
            b_day = int(billing_day)
        except (ValueError, TypeError):
            return None

        year = current_date.year
        month = current_date.month
        _, last_day = calendar.monthrange(year, month)
        target = date(year, month, min(b_day, last_day))

        if current_date <= target:
            return target
        else:
            # Move to next month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
            _, next_last = calendar.monthrange(year, month)
            return date(year, month, min(b_day, next_last))

    return None


async def get_connection_usage_pct(
    pool: DbPool,
    connection_id: str,
    now: datetime | None = None,
) -> float:
    """Get the highest usage percentage across all consumption units for this connection."""
    current = now or datetime.now(UTC)

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select unit, limit_value, period, reset_anchor from public.consumption_units "
            "where connection_id = %s",
            (connection_id,),
        )
        units = await cur.fetchall()

    if not units:
        return 0.0

    max_pct = 0.0
    for unit, limit_val, period, anchor in units:
        if limit_val is None or limit_val <= 0:
            continue

        async with pool.connection() as conn:
            cur_p = await conn.execute(
                "select public.farm_period_start(%s, %s, %s)",
                (period, anchor, current),
            )
            p_row = await cur_p.fetchone()
            period_start = p_row[0] if p_row else current

            cur_u = await conn.execute(
                "select coalesce(sum(used), 0) from public.quota_usage "
                "where connection_id = %s and unit = %s and period_start = %s",
                (connection_id, unit, period_start),
            )
            u_row = await cur_u.fetchone()
            used = float(u_row[0]) if u_row else 0.0

        pct = (used / float(limit_val)) * 100.0
        if pct > max_pct:
            max_pct = pct

    return max_pct


async def check_renewal_reminders(
    pool: DbPool,
    now: datetime | None = None,
) -> list[UUID]:
    """Check all connections and generate renewal reminder alerts for those renewing in 3 days.

    Deduplicated so each renewal date produces at most one alert.
    """
    current = now or datetime.now(UTC)
    current_date = current.date()

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select c.id, c.provider_id, c.label, c.plan, p.config "
            "from public.connections c join public.providers p on c.provider_id = p.id "
            "where c.status != 'disabled'"
        )
        rows = await cur.fetchall()

    alert_ids: list[UUID] = []

    for conn_id, _provider_id, _label, plan_raw, _prov_config in rows:
        plan: dict[str, Any] = plan_raw or {}
        renewal_date = compute_next_renewal_date(plan, current)
        if renewal_date is None:
            continue

        days_until = (renewal_date - current_date).days
        # Fire reminder when 3 days before renewal
        if days_until == 3:
            ref_id = f"renewal:{conn_id}:{renewal_date.isoformat()}"

            # Check if alert already exists for this renewal
            async with pool.connection() as conn:
                cur_exist = await conn.execute(
                    "select id from public.alerts where ref = %s",
                    (ref_id,),
                )
                if await cur_exist.fetchone() is not None:
                    continue

            usage_pct = await get_connection_usage_pct(pool, conn_id, current)
            plan_name = plan.get("name", "standard")
            price = plan.get("price_usd", 0)

            # Suggestion: keep if usage >= 50%, else cancel
            if usage_pct >= 50.0:
                suggestion = "keep (usage is healthy)"
            else:
                suggestion = "cancel (low usage, consider downgrading or cancelling)"

            msg = (
                f"Renewal reminder: Connection '{conn_id}' ({plan_name}, ${price}/mo) "
                f"renews on {renewal_date.isoformat()} (in {days_until} days). "
                f"Current period usage: {usage_pct:.1f}%. Suggestion: {suggestion}."
            )

            aid = await create_alert(
                pool,
                kind="renewal_reminder",
                severity="info",
                message=msg,
                ref=ref_id,
                deduplicate=True,
            )
            alert_ids.append(aid)

    return alert_ids


async def request_human_task(
    pool: DbPool,
    connection_id: str,
    action: str,
    note: str | None = None,
    billing_url: str | None = None,
) -> UUID:
    """Create a human_task alert for manual upgrade or cancellation. Never automates payments."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select c.meta, c.plan, p.config, p.id from public.connections c "
            "join public.providers p on c.provider_id = p.id where c.id = %s",
            (connection_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"Unknown connection '{connection_id}'")

        meta = row[0] or {}
        plan = row[1] or {}
        prov_cfg = row[2] or {}
        prov_id = row[3]

    url = (
        billing_url
        or meta.get("billing_url")
        or plan.get("billing_url")
        or prov_cfg.get("billing_url")
        or f"https://{prov_id}.com/billing"
    )

    msg = (
        f"Human task required: {action.upper()} subscription for connection '{connection_id}'. "
        f"Provider billing portal: {url}"
    )
    if note:
        msg += f" (Note: {note})"

    ref_id = f"human_task:{connection_id}:{action}"
    return await create_alert(
        pool,
        kind="human_task",
        severity="warn",
        message=msg,
        ref=ref_id,
        deduplicate=True,
    )
