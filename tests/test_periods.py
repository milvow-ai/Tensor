"""The Python period arithmetic must equal the SQL function the ledger uses, on every kind of boundary."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from farm.db.pool import DbPool
from farm.resources.periods import EPOCH, next_period_start, period_start

BOUNDED = ["minute", "hour", "day", "week", "month"]
UNBOUNDED = ["rolling_5h", "total", "none"]


def sample_instants(n: int, seed: int) -> list[datetime]:
    rng = random.Random(seed)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    instants = [base + timedelta(seconds=rng.randrange(0, 3 * 365 * 24 * 3600)) for _ in range(n)]
    # the awkward ones: month ends, leap day, year change, exact boundaries
    instants += [
        datetime(2024, 2, 29, 23, 59, 59, 999999, tzinfo=UTC),
        datetime(2024, 3, 1, tzinfo=UTC),
        datetime(2025, 2, 28, 12, tzinfo=UTC),
        datetime(2025, 12, 31, 23, 59, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 4, 30, 0, 0, 1, tzinfo=UTC),
    ]
    return instants


@pytest.mark.parametrize("period", BOUNDED + UNBOUNDED)
async def test_period_start_matches_the_sql_function(pool: DbPool, period: str) -> None:
    anchors: list[int | None] = [None, 1, 14, 28, 29, 30, 31] if period == "month" else [None]
    for anchor in anchors:
        instants = sample_instants(120, seed=anchors.index(anchor))
        async with pool.connection() as conn:
            for at in instants:
                cur = await conn.execute("select public.farm_period_start(%s, %s, %s)", (period, anchor, at))
                row = await cur.fetchone()
                assert row is not None
                assert period_start(period, anchor, at) == row[0], (period, anchor, at)


@pytest.mark.parametrize("period", BOUNDED)
def test_next_period_start_is_the_next_boundary(period: str) -> None:
    anchors: list[int | None] = [None, 1, 15, 29, 30, 31] if period == "month" else [None]
    for anchor in anchors:
        for at in sample_instants(300, seed=7):
            start = period_start(period, anchor, at)
            nxt = next_period_start(period, anchor, at)
            assert nxt is not None and nxt > at >= start
            assert period_start(period, anchor, nxt) == nxt  # it is itself a boundary
            assert (
                period_start(period, anchor, nxt - timedelta(microseconds=1)) == start
            )  # and the first one after


@pytest.mark.parametrize("period", UNBOUNDED)
def test_unbounded_periods_never_reset(period: str) -> None:
    at = datetime(2026, 10, 4, 12, tzinfo=UTC)
    assert period_start(period, None, at) == EPOCH
    assert next_period_start(period, None, at) is None


def test_month_anchor_31_clamps_to_the_short_months() -> None:
    assert period_start("month", 31, datetime(2026, 2, 28, 12, tzinfo=UTC)) == datetime(
        2026, 2, 28, tzinfo=UTC
    )
    assert next_period_start("month", 31, datetime(2026, 2, 28, 12, tzinfo=UTC)) == datetime(
        2026, 3, 31, tzinfo=UTC
    )
    assert next_period_start("month", 31, datetime(2026, 4, 30, 12, tzinfo=UTC)) == datetime(
        2026, 5, 31, tzinfo=UTC
    )


def test_the_week_starts_on_monday() -> None:
    sunday = datetime(2026, 10, 4, 23, 59, tzinfo=UTC)  # 2026-10-04 is a Sunday
    assert period_start("week", None, sunday) == datetime(2026, 9, 28, tzinfo=UTC)
    assert next_period_start("week", None, sunday) == datetime(2026, 10, 5, tzinfo=UTC)


def test_an_unknown_period_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown period"):
        period_start("fortnight", None, datetime(2026, 1, 1, tzinfo=UTC))
