"""Manager scheduler for periodic maintenance tasks.

Runs background tasks (or single maintenance ticks):
- Every 5 min: expire overdue quota reservations + auto-reactivate exhausted connections due for reset.
- Hourly: sync provider balances via API adapters.
- Daily: check budget thresholds (50/80/100%) and renewal reminders (3 days before billing).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from farm.db.pool import DbPool, tenant_connection
from farm.manager.balance import sync_all_balances
from farm.manager.budgets import check_budget_thresholds
from farm.manager.renewals import check_renewal_reminders
from farm.resources import ledger
from farm.tenancy.active import for_each_tenant

log = structlog.get_logger(__name__)


async def _everywhere[T](pool: DbPool, work: Callable[[], Awaitable[list[T]]], task: str) -> list[T]:
    """``work`` for every active tenant, the results joined."""
    return [item for found in await for_each_tenant(pool, work, task=task) for item in found]


async def reactivate_due_connections(
    pool: DbPool,
    now: datetime | None = None,
) -> list[str]:
    """Auto-resume exhausted connections whose reset time has arrived.

    An account is never resumed while its ``cooldown_until`` (what the provider said, or the next period
    start) is in the future. Past that, it resumes when a unit's ``next_reset_at`` has come, or its quota
    period has rolled over since the unit was last full. A resumed connection loses the ``next_reset_at``
    values that came due: the window has reset, and a stale date must not undo the next exhaustion.
    """
    current = now or datetime.now(UTC)
    reactivated: list[str] = []

    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select c.id, h.cooldown_until from public.connections c "
            "left join public.connection_health h on h.connection_id = c.id "
            "where c.status = 'exhausted'"
        )
        exhausted = await cur.fetchall()

    for conn_id, cooldown_until in exhausted:
        if cooldown_until is not None and cooldown_until > current:
            continue
        should_resume = False

        async with tenant_connection(pool) as conn:
            cur_cu = await conn.execute(
                "select unit, limit_value, period, reset_anchor, next_reset_at "
                "from public.consumption_units where connection_id = %s",
                (conn_id,),
            )
            units = await cur_cu.fetchall()

            for unit, _limit_val, period, anchor, next_reset_at in units:
                if next_reset_at is not None:
                    if current >= next_reset_at:
                        should_resume = True
                        break
                    continue

                if period in ("minute", "hour", "day", "week", "month"):
                    cur_p = await conn.execute(
                        "select public.farm_period_start(%s, %s, %s)",
                        (period, anchor, current),
                    )
                    p_row = await cur_p.fetchone()
                    period_start = p_row[0] if p_row else current

                    cur_last = await conn.execute(
                        "select max(period_start) from public.quota_usage "
                        "where connection_id = %s and unit = %s "
                        "and (limit_value is not null and used + reserved >= limit_value)",
                        (conn_id, unit),
                    )
                    last_row = await cur_last.fetchone()
                    if last_row and last_row[0] is not None:
                        if period_start > last_row[0]:
                            should_resume = True
                            break

        if should_resume:
            from psycopg.types.json import Jsonb

            async with tenant_connection(pool) as conn, conn.transaction():
                cur_up = await conn.execute(
                    "update public.connections set status = 'active' where id = %s and status = 'exhausted' "
                    "returning id",
                    (conn_id,),
                )
                if await cur_up.fetchone() is None:
                    continue
                await conn.execute(
                    "update public.consumption_units set next_reset_at = null "
                    "where connection_id = %s and next_reset_at is not null and next_reset_at <= %s",
                    (conn_id, current),
                )
                await conn.execute(
                    "insert into public.audit_events (actor, action, target, before, after) "
                    "values (%s, %s, %s, %s, %s)",
                    (
                        "manager_scheduler",
                        "auto_resume",
                        conn_id,
                        Jsonb({"status": "exhausted"}),
                        Jsonb({"status": "active"}),
                    ),
                )
            log.info("manager.auto_resumed_connection", connection_id=conn_id)
            reactivated.append(conn_id)

    return reactivated


async def run_maintenance_tick(
    pool: DbPool,
    now: datetime | None = None,
    *,
    sync_balances: bool = False,
) -> dict[str, Any]:
    """Execute a single maintenance pass across all manager tasks (ideal for testing)."""
    current = now or datetime.now(UTC)

    # 1. Expire reservations
    expired = await ledger.expire(pool)

    # The rest is tenant work: it runs once for every active tenant, inside that tenant's context.
    # 2. Reactivate due connections
    reactivated = await _everywhere(pool, lambda: reactivate_due_connections(pool, current), "reactivate")

    # 3. Budget thresholds
    budget_alerts = await _everywhere(pool, lambda: check_budget_thresholds(pool, current), "budgets")

    # 4. Renewal reminders
    renewal_alerts = await _everywhere(pool, lambda: check_renewal_reminders(pool, current), "renewals")

    # 5. Balances (optional)
    balance_syncs: dict[str, list[dict[str, Any]]] = {}
    if sync_balances:
        for synced in await for_each_tenant(pool, lambda: sync_all_balances(pool), task="balances"):
            for provider, entries in synced.items():
                balance_syncs.setdefault(provider, []).extend(entries)

    return {
        "expired_reservations": expired,
        "reactivated_connections": reactivated,
        "budget_alerts": budget_alerts,
        "renewal_alerts": renewal_alerts,
        "balance_syncs": balance_syncs,
    }


class ManagerScheduler:
    """Asyncio loop managing periodic tasks."""

    def __init__(self, pool: DbPool) -> None:
        self.pool = pool
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("manager_scheduler.started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("manager_scheduler.stopped")

    async def _loop(self) -> None:
        tick_1h = 0
        tick_24h = 0

        while self._running:
            try:
                # 5-min tasks: expire reservations, reactivate exhausted
                await ledger.expire(self.pool)
                await _everywhere(self.pool, lambda: reactivate_due_connections(self.pool), "reactivate")

                tick_1h += 1
                if tick_1h >= 12:  # 12 * 5m = 60m
                    tick_1h = 0
                    await for_each_tenant(self.pool, lambda: sync_all_balances(self.pool), task="balances")

                tick_24h += 1
                if tick_24h >= 288:  # 288 * 5m = 24h
                    tick_24h = 0
                    await _everywhere(self.pool, lambda: check_budget_thresholds(self.pool), "budgets")
                    await _everywhere(self.pool, lambda: check_renewal_reminders(self.pool), "renewals")

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("manager_scheduler.tick_error", error=str(exc))

            try:
                await asyncio.sleep(300)  # 5 minutes
            except asyncio.CancelledError:
                break
