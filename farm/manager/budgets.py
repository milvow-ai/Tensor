"""Budget management, forecasting, threshold alerting, and router hard-stop policy.

Supports:
- Month-to-date spend per connection, provider, and global from usage_events + billing_events
- Month-end spend forecasting
- Threshold alerts (50%, 80%, 100%) exactly once per month per scope
- Router hard-stop policy decision pure function (check_paid_call) that refuses paid calls
  exceeding a hard stop budget.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel

from farm.db.pool import DbPool
from farm.manager.alerts import create_alert

log = structlog.get_logger(__name__)


class PolicyDecision(BaseModel):
    """Result of checking router budget policies before a call."""

    allowed: bool
    reason: str | None = None
    scope: str | None = None
    budget_usd: Decimal | None = None
    current_spend_usd: Decimal | None = None
    estimated_cost_usd: Decimal = Decimal(0)
    error_kind: str | None = None  # e.g. "budget_exhausted"

    def __bool__(self) -> bool:
        return self.allowed


async def get_month_to_date_spend(
    pool: DbPool,
    scope: str,
    ref: str | None = None,
    now: datetime | None = None,
) -> Decimal:
    """Calculate month-to-date spend from usage_events and billing_events.

    Scope can be 'connection', 'provider', or 'global'.
    """
    current = now or datetime.now(UTC)
    month_start = datetime(current.year, current.month, 1, tzinfo=UTC)

    async with pool.connection() as conn:
        if scope == "connection":
            if not ref:
                raise ValueError("ref (connection_id) required for connection scope")
            cur_u = await conn.execute(
                "select coalesce(sum(cost_usd), 0) from public.usage_events "
                "where connection_id = %s and at >= %s",
                (ref, month_start),
            )
            cur_b = await conn.execute(
                "select coalesce(sum(case when kind = 'refund' then -amount_usd else amount_usd end), 0) "
                "from public.billing_events where connection_id = %s and at >= %s",
                (ref, month_start),
            )
        elif scope == "provider":
            if not ref:
                raise ValueError("ref (provider_id) required for provider scope")
            cur_u = await conn.execute(
                "select coalesce(sum(u.cost_usd), 0) from public.usage_events u "
                "join public.connections c on u.connection_id = c.id "
                "where c.provider_id = %s and u.at >= %s",
                (ref, month_start),
            )
            cur_b = await conn.execute(
                "select coalesce(sum(case when b.kind = 'refund' "
                "then -b.amount_usd else b.amount_usd end), 0) "
                "from public.billing_events b "
                "join public.connections c on b.connection_id = c.id "
                "where c.provider_id = %s and b.at >= %s",
                (ref, month_start),
            )
        elif scope == "global":
            cur_u = await conn.execute(
                "select coalesce(sum(cost_usd), 0) from public.usage_events where at >= %s",
                (month_start,),
            )
            cur_b = await conn.execute(
                "select coalesce(sum(case when kind = 'refund' then -amount_usd else amount_usd end), 0) "
                "from public.billing_events where at >= %s",
                (month_start,),
            )
        else:
            raise ValueError(f"Unknown scope '{scope}'")

        u_row = await cur_u.fetchone()
        b_row = await cur_b.fetchone()
        usage_spend = Decimal(str(u_row[0])) if u_row else Decimal(0)
        billing_spend = Decimal(str(b_row[0])) if b_row else Decimal(0)

    return max(Decimal(0), usage_spend + billing_spend)


def calculate_month_forecast(spend: Decimal | float | int, now: datetime | None = None) -> Decimal:
    """Forecast month-end spend: spend ÷ elapsed days × total days in month."""
    current = now or datetime.now(UTC)
    sp = Decimal(str(spend))
    if sp <= 0:
        return Decimal(0)

    _, total_days = calendar.monthrange(current.year, current.month)
    elapsed = max(1.0, current.day + (current.hour / 24.0) + (current.minute / 1440.0))
    return (sp / Decimal(str(elapsed))) * Decimal(total_days)


async def check_paid_call(
    pool: DbPool,
    connection_id: str,
    est_cost: Decimal | float | int,
    *,
    now: datetime | None = None,
) -> PolicyDecision:
    """Pure policy function for router: checks whether a call crossing hard-stop budgets should be blocked.

    Free calls (est_cost <= 0) are always allowed.
    Paid calls (est_cost > 0) check connection, provider, and global hard-stop budgets.
    """
    cost = Decimal(str(est_cost))
    if cost <= Decimal(0):
        return PolicyDecision(allowed=True, estimated_cost_usd=cost)

    current = now or datetime.now(UTC)

    async with pool.connection() as conn:
        # Get provider for connection
        cur_c = await conn.execute(
            "select provider_id from public.connections where id = %s",
            (connection_id,),
        )
        c_row = await cur_c.fetchone()
        if c_row is None:
            return PolicyDecision(
                allowed=False,
                reason=f"Unknown connection '{connection_id}'",
                error_kind="unknown_connection",
            )
        provider_id = c_row[0]

        # Gather relevant budgets
        # 1. Connection budget
        cur_cb = await conn.execute(
            "select monthly_usd, hard_stop from public.budgets "
            "where scope = 'connection' and ref = %s",
            (connection_id,),
        )
        cb_row = await cur_cb.fetchone()

        # 2. Provider budget
        cur_pb = await conn.execute(
            "select monthly_usd, hard_stop from public.budgets "
            "where scope = 'provider' and ref = %s",
            (provider_id,),
        )
        pb_row = await cur_pb.fetchone()

        # 3. Global budget
        cur_gb = await conn.execute(
            "select monthly_usd, hard_stop from public.budgets "
            "where scope = 'global' and (ref is null or ref = 'global')",
        )
        gb_row = await cur_gb.fetchone()

        global_budget_val = None
        global_hard_stop = True
        if gb_row is not None:
            global_budget_val = Decimal(str(gb_row[0]))
            global_hard_stop = gb_row[1]
        else:
            cur_set = await conn.execute(
                "select global_monthly_budget_usd from public.farm_settings where id = 1"
            )
            set_row = await cur_set.fetchone()
            if set_row is not None and set_row[0] is not None:
                global_budget_val = Decimal(str(set_row[0]))

    # Check budgets with hard_stop enabled
    budgets_to_check: list[tuple[str, str | None, Decimal, bool]] = []
    if cb_row is not None and cb_row[1]:
        budgets_to_check.append(("connection", connection_id, Decimal(str(cb_row[0])), cb_row[1]))
    if pb_row is not None and pb_row[1]:
        budgets_to_check.append(("provider", provider_id, Decimal(str(pb_row[0])), pb_row[1]))
    if global_budget_val is not None and global_hard_stop:
        budgets_to_check.append(("global", None, global_budget_val, global_hard_stop))

    for scope, ref, budget_usd, _hard_stop in budgets_to_check:
        spend = await get_month_to_date_spend(pool, scope, ref, now=current)
        if spend + cost > budget_usd:
            log.warning(
                "budget.hard_stop_exceeded",
                scope=scope,
                ref=ref,
                budget=float(budget_usd),
                spend=float(spend),
                cost=float(cost),
            )
            return PolicyDecision(
                allowed=False,
                reason=f"hard stop: {scope} budget of ${budget_usd} exceeded (spend ${spend} + est ${cost})",
                scope=scope,
                budget_usd=budget_usd,
                current_spend_usd=spend,
                estimated_cost_usd=cost,
                error_kind="budget_exhausted",
            )

    return PolicyDecision(allowed=True, estimated_cost_usd=cost)


async def record_policy_block(
    pool: DbPool,
    run_id: UUID | str,
    connection_id: str | None,
    reason: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Record a policy_block event into run_events for trajectory observability."""
    rid = run_id if isinstance(run_id, UUID) else UUID(str(run_id))
    event_data = dict(data or {})
    event_data["reason"] = reason

    async with pool.connection() as conn:
        # Determine next seq for run_id
        cur = await conn.execute(
            "select coalesce(max(seq), 0) + 1 from public.run_events where run_id = %s",
            (rid,),
        )
        seq_row = await cur.fetchone()
        seq = seq_row[0] if seq_row else 1

        from psycopg.types.json import Jsonb

        await conn.execute(
            "insert into public.run_events (run_id, seq, kind, connection_id, data) "
            "values (%s, %s, 'policy_block', %s, %s)",
            (rid, seq, connection_id, Jsonb(event_data)),
        )


async def check_budget_thresholds(
    pool: DbPool,
    now: datetime | None = None,
) -> list[UUID]:
    """Check spend thresholds (50%, 80%, 100%) across all budgets.

    Creates alerts once per month per scope.
    """
    current = now or datetime.now(UTC)
    month_key = f"{current.year}-{current.month:02d}"

    async with pool.connection() as conn:
        # Fetch alert thresholds from settings
        cur_set = await conn.execute(
            "select alert_thresholds, global_monthly_budget_usd from public.farm_settings where id = 1"
        )
        set_row = await cur_set.fetchone()
        thresholds: list[int] = [50, 80, 100]
        global_setting_budget = None
        if set_row is not None:
            if set_row[0]:
                thresholds = list(set_row[0])
            if set_row[1] is not None:
                global_setting_budget = Decimal(str(set_row[1]))

        # Fetch all budgets
        cur_b = await conn.execute(
            "select scope, ref, monthly_usd from public.budgets"
        )
        rows = await cur_b.fetchall()

    budget_list: list[tuple[str, str | None, Decimal]] = [
        (row[0], row[1], Decimal(str(row[2]))) for row in rows
    ]

    # Include settings global budget if not already in budgets table
    has_global = any(s == "global" for s, _, _ in budget_list)
    if not has_global and global_setting_budget is not None:
        budget_list.append(("global", None, global_setting_budget))

    alert_ids: list[UUID] = []

    for scope, ref, monthly_usd in budget_list:
        if monthly_usd <= 0:
            continue

        spend = await get_month_to_date_spend(pool, scope, ref, now=current)
        spend_pct = (spend / monthly_usd) * 100

        for thresh in sorted(thresholds):
            if spend_pct >= thresh:
                ref_id = f"budget:{scope}:{ref or 'global'}:{thresh}:{month_key}"
                severity = "critical" if thresh >= 100 else ("warn" if thresh >= 80 else "info")
                forecast = calculate_month_forecast(spend, current)
                msg = (
                    f"Budget alert: {scope} {ref or 'global'} reached {thresh}% threshold "
                    f"(spend ${spend:.2f} of ${monthly_usd:.2f}, forecast ${forecast:.2f})"
                )
                aid = await create_alert(
                    pool,
                    kind="budget_threshold",
                    severity=severity,
                    message=msg,
                    ref=ref_id,
                    deduplicate=True,
                )
                alert_ids.append(aid)

    return alert_ids
