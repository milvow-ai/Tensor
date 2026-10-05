"""Budget checking and hard stop enforcement for the router."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from farm.db.pool import DbPool
from farm.manager.budgets import (
    PolicyDecision,
    check_budget_thresholds,
    check_paid_call,
    get_month_to_date_spend,
)

__all__ = [
    "PolicyDecision",
    "check",
    "check_budget_thresholds",
    "get_month_to_date_spend",
]


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
