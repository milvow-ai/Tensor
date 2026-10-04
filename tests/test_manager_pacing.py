"""Tests for farm/manager/pacing.py: daily pacing budget, over-pace detection, and rank penalty."""

from datetime import UTC, datetime
from decimal import Decimal

from farm.db.pool import DbPool
from farm.manager.pacing import (
    compute_daily_pacing_budget,
    get_connection_pacing,
    is_over_pace,
    pace_rank_penalty,
)
from tests.conftest import seed_connection


def test_compute_daily_pacing_budget() -> None:
    # 300 credits remaining over 10 days = 30 credits/day
    assert compute_daily_pacing_budget(300, 10) == Decimal("30")

    # 100 credits remaining over 1 day = 100 credits/day
    assert compute_daily_pacing_budget(100, 1) == Decimal("100")

    # Clamps min days to 1
    assert compute_daily_pacing_budget(50, 0) == Decimal("50")

    # 0 remaining
    assert compute_daily_pacing_budget(0, 5) == Decimal("0")


def test_is_over_pace() -> None:
    assert is_over_pace(today_usage=35, daily_pacing_budget=30) is True
    assert is_over_pace(today_usage=30, daily_pacing_budget=30) is False
    assert is_over_pace(today_usage=25, daily_pacing_budget=30) is False


def test_pace_rank_penalty() -> None:
    # When over pace, returns default penalty (10,000) so connection ranks last
    assert pace_rank_penalty(today_usage=40, daily_pacing_budget=30) == 10000

    # When within budget, returns 0 penalty
    assert pace_rank_penalty(today_usage=20, daily_pacing_budget=30) == 0

    # Explicit flag
    assert pace_rank_penalty(is_over=True, penalty=5000) == 5000
    assert pace_rank_penalty(is_over=False) == 0


async def test_get_connection_pacing_from_db(pool: DbPool) -> None:
    # Monthly connection with 3000 credit limit
    await seed_connection(
        pool,
        "p_pace",
        "c_pace_1",
        units={"credits": {"limit": 3000, "period": "month", "anchor": 1}},
    )

    # Insert today's usage event of 150 credits
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.usage_events (connection_id, unit, amount, kind, cost_usd, at) "
            "values ('c_pace_1', 'credits', 150, 'actual', 0, %s)",
            (now,),
        )

    status = await get_connection_pacing(pool, "c_pace_1", unit="credits", now=now)
    assert status.connection_id == "c_pace_1"
    assert status.unit == "credits"
    # From Oct 15 to Nov 1 is 17 days
    assert status.days_to_reset == 17
    # 3000 limit / 17 days ≈ 176.47 daily budget
    assert status.daily_pacing_budget > Decimal("170")
    assert status.today_usage == Decimal("150")
    # 150 < 176.47 -> not over pace
    assert status.is_over_pace is False
    assert status.rank_penalty == 0

    # Now add another 100 credits of usage today -> total 250 > 176.47 -> over pace
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.usage_events (connection_id, unit, amount, kind, cost_usd, at) "
            "values ('c_pace_1', 'credits', 100, 'actual', 0, %s)",
            (now,),
        )

    status2 = await get_connection_pacing(pool, "c_pace_1", unit="credits", now=now)
    assert status2.today_usage == Decimal("250")
    assert status2.is_over_pace is True
    assert status2.rank_penalty == 10000
