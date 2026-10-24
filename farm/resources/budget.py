"""Budget checking and hard stop enforcement for the router.

``reserve`` is the router's gate: the estimated cost of a paid call is held against every applicable
hard-stop budget (connection, provider, global) under one database lock *before* the call, the way units are
reserved, and dropped by ``release`` once the call settled (its real cost is then in ``usage_events``).
Concurrent calls see each other's holds, so they cannot overshoot a hard stop. ``check`` is the same
decision without a hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from farm.db.pool import DbPool, tenant_connection
from farm.manager.budgets import (
    PolicyDecision,
    check_budget_thresholds,
    check_paid_call,
    decide_hold,
    get_month_to_date_spend,
)

__all__ = [
    "BudgetHold",
    "PolicyDecision",
    "check",
    "check_budget_thresholds",
    "get_month_to_date_spend",
    "release",
    "reserve",
]

log = structlog.get_logger(__name__)

DEFAULT_HOLD_TTL_S = 360


@dataclass(frozen=True)
class BudgetHold:
    """The decision for one paid call and, when it is allowed, the hold to release after settling."""

    decision: PolicyDecision
    hold_id: UUID | None = None


async def check(
    pool: DbPool,
    connection_id: str,
    est_cost: Decimal | float | int,
    *,
    now: datetime | None = None,
) -> PolicyDecision:
    """Check budget policy before reserving a call.

    Free calls (est_cost <= 0) are never blocked.
    Paid calls (est_cost > 0) are refused if any applicable hard-stop budget
    (connection, provider, or global) is exceeded.
    Soft budgets trigger alerts at 50%, 80%, and 100% thresholds (once per period).
    """
    current = now or datetime.now(UTC)
    cost = Decimal(str(est_cost))
    if cost <= Decimal(0):
        return PolicyDecision(allowed=True, estimated_cost_usd=cost)

    decision = await check_paid_call(pool, connection_id, cost, now=current)

    # Check soft budgets / threshold alerts (does not block)
    try:
        await check_budget_thresholds(pool, now=current)
    except Exception:
        pass

    return decision


async def reserve(
    pool: DbPool,
    connection_id: str,
    est_cost: Decimal | float | int,
    *,
    request_id: UUID | None = None,
    ttl_s: int = DEFAULT_HOLD_TTL_S,
    now: datetime | None = None,
) -> BudgetHold:
    """Hold ``est_cost`` against the hard-stop budgets, or refuse the call (``budget_exhausted``).

    Free calls are never held or blocked. A hold that is never released (a crashed process) stops counting
    when its ``ttl_s`` has run out.
    """
    current = now or datetime.now(UTC)
    cost = Decimal(str(est_cost))
    if cost <= Decimal(0):
        return BudgetHold(PolicyDecision(allowed=True, estimated_cost_usd=cost))

    month_start = datetime(current.year, current.month, 1, tzinfo=UTC)
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select public.farm_budget_reserve(%s::text, %s::numeric, %s::uuid, %s::timestamptz, "
            "%s::timestamptz, %s::int)",
            (connection_id, cost, request_id, current, month_start, ttl_s),
        )
        row = await cur.fetchone()
    outcome: dict[str, Any] = row[0] if row is not None and row[0] is not None else {}

    decision, hold_id = decide_hold(connection_id, cost, outcome)
    if hold_id is None:
        return BudgetHold(decision)

    try:
        await check_budget_thresholds(pool, now=current)
    except Exception:
        log.warning("budget.threshold_check_failed", exc_info=True)
    return BudgetHold(decision, hold_id)


async def release(pool: DbPool, hold_id: UUID) -> None:
    """Drop a hold once its call settled (the spend itself is in ``usage_events`` by then). Idempotent."""
    async with tenant_connection(pool) as conn:
        await conn.execute("delete from public.budget_reservations where id = %s", (hold_id,))
