"""Daily pacing budget and router ranking penalties.

Calculates:
- daily pacing budget = remaining ÷ days to reset
- checks if today's usage exceeds the daily budget
- applies rank penalty to over-pace connections so they rank last in router selection
  rather than being blocked.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel

from farm.db.pool import DbPool


class PacingStatus(BaseModel):
    connection_id: str
    unit: str
    remaining: Decimal
    days_to_reset: int
    daily_pacing_budget: Decimal
    today_usage: Decimal
    is_over_pace: bool
    rank_penalty: int


def compute_daily_pacing_budget(
    remaining: Decimal | float | int,
    days_to_reset: float | int,
) -> Decimal:
    """Calculate the daily pacing budget: remaining ÷ days to reset (min 1 day)."""
    days = max(1, int(math.ceil(days_to_reset)))
    rem = Decimal(str(remaining))
    if rem <= 0:
        return Decimal(0)
    return rem / Decimal(days)


def is_over_pace(
    today_usage: Decimal | float | int,
    daily_pacing_budget: Decimal | float | int,
) -> bool:
    """Return True if today's usage exceeds the daily pacing budget."""
    return Decimal(str(today_usage)) > Decimal(str(daily_pacing_budget))


def pace_rank_penalty(
    today_usage: Decimal | float | int | None = None,
    daily_pacing_budget: Decimal | float | int | None = None,
    *,
    is_over: bool | None = None,
    penalty: int = 10000,
) -> int:
    """Pure function for router ranking.

    Returns a ranking penalty (default 10,000) if the connection is over pace,
    causing it to rank last in candidate selection. Otherwise returns 0.
    """
    if is_over is True:
        return penalty
    if is_over is False:
        return 0
    if today_usage is not None and daily_pacing_budget is not None:
        if is_over_pace(today_usage, daily_pacing_budget):
            return penalty
    return 0


def days_until_reset(
    period: str,
    anchor: int | None,
    next_reset_at: datetime | None = None,
    now: datetime | None = None,
) -> int:
    """Estimate number of days remaining until the next quota reset."""
    current = now or datetime.now(UTC)
    if next_reset_at is not None:
        delta = next_reset_at - current
        return max(1, int(math.ceil(delta.total_seconds() / 86400.0)))

    if period == "day":
        return 1

    if period == "week":
        # Days until Sunday midnight / Monday 00:00
        days_left = 7 - current.weekday()
        return max(1, days_left)

    if period == "month":
        day_anchor = max(1, min(anchor or 1, 31))
        # Find next occurrence of day_anchor
        year = current.year
        month = current.month
        # Try this month
        import calendar

        _, last_day = calendar.monthrange(year, month)
        target_day = min(day_anchor, last_day)
        target_dt = datetime(year, month, target_day, tzinfo=UTC)
        if current >= target_dt:
            # Move to next month
            if month == 12:
                year += 1
                month = 1
            else:
                month += 1
            _, next_last_day = calendar.monthrange(year, month)
            target_dt = datetime(year, month, min(day_anchor, next_last_day), tzinfo=UTC)
        delta = target_dt - current
        return max(1, int(math.ceil(delta.total_seconds() / 86400.0)))

    return 30  # Default fallback


async def get_connection_pacing(
    pool: DbPool,
    connection_id: str,
    unit: str = "credits",
    now: datetime | None = None,
) -> PacingStatus:
    """Query database to compute current pacing metrics for a connection and unit."""
    current = now or datetime.now(UTC)

    async with pool.connection() as conn:
        # 1. Get consumption unit details
        cur = await conn.execute(
            "select limit_value, period, reset_anchor, next_reset_at from public.consumption_units "
            "where connection_id = %s and unit = %s",
            (connection_id, unit),
        )
        cu_row = await cur.fetchone()
        if cu_row is None:
            raise ValueError(f"Unit '{unit}' not configured for connection '{connection_id}'")

        limit_val = Decimal(str(cu_row[0])) if cu_row[0] is not None else Decimal(0)
        period = cu_row[1]
        anchor = cu_row[2]
        next_reset_at = cu_row[3]

        # 2. Get current period usage
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
        used = Decimal(str(u_row[0])) if u_row else Decimal(0)

        remaining = max(Decimal(0), limit_val - used) if limit_val > 0 else Decimal(0)

        # 3. Get today's usage from usage_events
        cur_day = await conn.execute(
            "select public.farm_period_start('day', null, %s)",
            (current,),
        )
        day_row = await cur_day.fetchone()
        day_start = day_row[0] if day_row else current

        cur_today = await conn.execute(
            "select coalesce(sum(amount), 0) from public.usage_events "
            "where connection_id = %s and unit = %s and at >= %s",
            (connection_id, unit, day_start),
        )
        today_row = await cur_today.fetchone()
        today_usage = Decimal(str(today_row[0])) if today_row else Decimal(0)

    days_left = days_until_reset(period, anchor, next_reset_at, current)
    daily_budget = compute_daily_pacing_budget(remaining, days_left)
    over = is_over_pace(today_usage, daily_budget)
    penalty = pace_rank_penalty(is_over=over)

    return PacingStatus(
        connection_id=connection_id,
        unit=unit,
        remaining=remaining,
        days_to_reset=days_left,
        daily_pacing_budget=daily_budget,
        today_usage=today_usage,
        is_over_pace=over,
        rank_penalty=penalty,
    )
