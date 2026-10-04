"""Quota ledger behaviour: reserve / commit / release / expire and the period arithmetic."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from psycopg import errors

from farm.db.pool import DbPool
from farm.resources import ledger
from tests.conftest import seed_connection


async def usage(pool: DbPool, conn_id: str = "c", unit: str = "credits") -> tuple[Decimal, Decimal]:
    """(used, reserved) summed over all periods of the unit."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select coalesce(sum(used), 0), coalesce(sum(reserved), 0) from quota_usage "
            "where connection_id = %s and unit = %s",
            (conn_id, unit),
        )
        row = await cur.fetchone()
    assert row is not None
    return row[0], row[1]


async def reservation(pool: DbPool, res_id: UUID) -> dict[str, Any]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select status, amount, actual, request_id, expires_at, now() from quota_reservations where id = %s",
            (res_id,),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(zip(("status", "amount", "actual", "request_id", "expires_at", "now"), row, strict=True))


# --- reserve -----------------------------------------------------------------------------------------------


async def test_reserve_within_limit_returns_id(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    rid = uuid4()
    res = await ledger.reserve(pool, "c", "credits", 4, rid)
    assert isinstance(res, UUID)
    assert await usage(pool) == (0, 4)
    info = await reservation(pool, res)
    assert info["status"] == "reserved" and info["amount"] == 4 and info["request_id"] == rid
    assert info["actual"] is None


async def test_reserve_over_limit_returns_none_and_changes_nothing(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    assert await ledger.reserve(pool, "c", "credits", 11, uuid4()) is None
    assert await usage(pool) == (0, 0)
    async with pool.connection() as conn:
        cur = await conn.execute("select count(*) from quota_reservations")
        assert await cur.fetchone() == (0,)


async def test_reserve_up_to_exactly_the_limit(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    assert await ledger.reserve(pool, "c", "credits", 6, uuid4()) is not None
    assert await ledger.reserve(pool, "c", "credits", 4, uuid4()) is not None  # used + reserved == limit
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None
    assert await usage(pool) == (0, 10)


async def test_reserve_counts_used_and_reserved(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    first = await ledger.reserve(pool, "c", "credits", 5, uuid4())
    assert first is not None
    await ledger.commit(pool, first, 5)  # used 5
    assert await ledger.reserve(pool, "c", "credits", 3, uuid4()) is not None  # reserved 3
    assert await ledger.reserve(pool, "c", "credits", 3, uuid4()) is None  # 5 + 3 + 3 > 10
    assert await ledger.reserve(pool, "c", "credits", 2, uuid4()) is not None


async def test_reserve_accepts_float_decimal_and_str_ids(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 1})
    for _ in range(10):
        assert (
            await ledger.reserve(pool, "c", "credits", 0.1, str(uuid4())) is not None
        )  # exact, no float noise
    assert await ledger.reserve(pool, "c", "credits", Decimal("0.1"), None) is None
    assert await usage(pool) == (0, Decimal("1.0"))


async def test_reserve_unknown_unit_raises(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 1})
    with pytest.raises(errors.NoDataFound, match="unknown unit nope"):
        await ledger.reserve(pool, "c", "nope", 1, uuid4())
    with pytest.raises(errors.NoDataFound):
        await ledger.reserve(pool, "missing", "credits", 1, uuid4())


async def test_negative_amounts_are_rejected(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 5})
    with pytest.raises(errors.InvalidParameterValue):
        await ledger.reserve(pool, "c", "credits", -1, uuid4())
    res = await ledger.reserve(pool, "c", "credits", 1, uuid4())
    assert res is not None
    with pytest.raises(errors.InvalidParameterValue):
        await ledger.commit(pool, res, -1)
    assert await usage(pool) == (0, 1)


async def test_units_and_connections_are_independent(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c1", {"credits": 2, "calls": 1})
    await seed_connection(pool, "p", "c2", {"credits": 2})
    assert await ledger.reserve(pool, "c1", "credits", 2, uuid4()) is not None
    assert await ledger.reserve(pool, "c1", "credits", 1, uuid4()) is None
    assert await ledger.reserve(pool, "c1", "calls", 1, uuid4()) is not None
    assert await ledger.reserve(pool, "c2", "credits", 2, uuid4()) is not None


async def test_null_limit_is_unlimited(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": None})
    for _ in range(5):
        assert await ledger.reserve(pool, "c", "credits", 10**9, uuid4()) is not None
    assert await usage(pool) == (0, 5 * 10**9)
    async with pool.connection() as conn:
        cur = await conn.execute("select limit_value from quota_usage")
        assert await cur.fetchone() == (None,)


async def test_changing_the_limit_applies_to_the_next_reserve(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 5})
    assert await ledger.reserve(pool, "c", "credits", 5, uuid4()) is not None
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None

    async def set_limit(value: int | None) -> None:
        async with pool.connection() as conn:
            await conn.execute(
                "update consumption_units set limit_value = %s where connection_id = 'c'", (value,)
            )

    await set_limit(8)  # raised
    assert await ledger.reserve(pool, "c", "credits", 3, uuid4()) is not None
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None
    await set_limit(2)  # lowered below what is already reserved
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None
    await set_limit(None)  # unlimited
    assert await ledger.reserve(pool, "c", "credits", 1000, uuid4()) is not None
    async with pool.connection() as conn:
        cur = await conn.execute("select limit_value from quota_usage")
        assert await cur.fetchone() == (None,)


async def test_ttl_sets_expiry(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 5})
    res = await ledger.reserve(pool, "c", "credits", 1, uuid4(), ttl_s=120)
    assert res is not None
    info = await reservation(pool, res)
    assert 119 <= (info["expires_at"] - info["now"]).total_seconds() <= 121


async def test_new_period_starts_fresh(pool: DbPool) -> None:
    """Usage of a past period never counts against the current one; each period has its own row."""
    await seed_connection(pool, "p", "c", {"credits": {"limit": 3, "period": "day"}})
    async with pool.connection() as conn:
        await conn.execute(
            "insert into quota_usage (connection_id, unit, period_start, used, reserved, limit_value) "
            "values ('c', 'credits', date_trunc('day', now()) - interval '1 day', 3, 0, 3)"
        )
    assert await ledger.reserve(pool, "c", "credits", 3, uuid4()) is not None
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select period_start = public.farm_period_start('day', null, now()), used, reserved "
            "from quota_usage order by period_start"
        )
        rows = await cur.fetchall()
    assert rows == [(False, 3, 0), (True, 0, 3)]


async def test_total_period_uses_a_single_row(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "total"}})
    for _ in range(3):
        assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is not None
    async with pool.connection() as conn:
        cur = await conn.execute("select period_start, reserved from quota_usage")
        assert await cur.fetchall() == [(datetime(1970, 1, 1, tzinfo=UTC), 3)]


# --- commit / release -----------------------------------------------------------------------------------------


async def test_commit_with_actual_less_than_amount_frees_the_difference(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    res = await ledger.reserve(pool, "c", "credits", 5, uuid4())
    assert res is not None
    await ledger.commit(pool, res, 3)
    assert await usage(pool) == (3, 0)
    info = await reservation(pool, res)
    assert (info["status"], info["actual"]) == ("committed", 3)
    assert await ledger.reserve(pool, "c", "credits", 7, uuid4()) is not None  # 10 - 3 left
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None


async def test_commit_with_actual_zero_charges_nothing(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    res = await ledger.reserve(pool, "c", "credits", 5, uuid4())
    assert res is not None
    await ledger.commit(pool, res, 0)
    assert await usage(pool) == (0, 0)
    assert (await reservation(pool, res))["actual"] == 0


async def test_commit_without_actual_charges_the_reserved_amount(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    res = await ledger.reserve(pool, "c", "credits", 4, uuid4())
    assert res is not None
    await ledger.commit(pool, res)
    assert await usage(pool) == (4, 0)


async def test_commit_with_actual_above_amount_records_the_overrun(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    res = await ledger.reserve(pool, "c", "credits", 2, uuid4())
    assert res is not None
    await ledger.commit(pool, res, 3)  # the provider charged more than estimated: the truth wins
    assert await usage(pool) == (3, 0)


async def test_release_frees_the_reservation(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 5})
    res = await ledger.reserve(pool, "c", "credits", 5, uuid4())
    assert res is not None
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None
    await ledger.release(pool, res)
    assert await usage(pool) == (0, 0)
    assert (await reservation(pool, res))["status"] == "released"
    assert await ledger.reserve(pool, "c", "credits", 5, uuid4()) is not None


async def test_double_commit_and_double_release_are_no_ops(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    a = await ledger.reserve(pool, "c", "credits", 3, uuid4())
    b = await ledger.reserve(pool, "c", "credits", 2, uuid4())
    assert a is not None and b is not None
    await ledger.commit(pool, a, 3)
    await ledger.commit(pool, a, 3)
    await ledger.commit(pool, a, 99)  # a later, different actual must not be applied either
    await ledger.release(pool, a)  # release after commit: nothing
    await ledger.release(pool, b)
    await ledger.release(pool, b)
    await ledger.commit(pool, b, 2)  # commit after release: nothing
    assert await usage(pool) == (3, 0)
    assert (await reservation(pool, a))["status"] == "committed"
    assert (await reservation(pool, b))["status"] == "released"


async def test_unknown_reservation_is_a_no_op(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    await ledger.commit(pool, uuid4(), 1)
    await ledger.release(pool, uuid4())
    assert await ledger.expire(pool) == 0
    assert await usage(pool) == (0, 0)


# --- expiry ------------------------------------------------------------------------------------------------------


async def test_expire_releases_only_overdue_reservations(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    old = await ledger.reserve(pool, "c", "credits", 4, uuid4(), ttl_s=60)
    fresh = await ledger.reserve(pool, "c", "credits", 3, uuid4(), ttl_s=3600)
    done = await ledger.reserve(pool, "c", "credits", 1, uuid4(), ttl_s=60)
    assert old is not None and fresh is not None and done is not None
    await ledger.commit(pool, done, 1)
    async with pool.connection() as conn:
        await conn.execute(
            "update quota_reservations set expires_at = now() - interval '1 minute' where id in (%s, %s)",
            (old, done),
        )
    assert await ledger.expire(pool) == 1  # only the still-reserved overdue one
    assert await usage(pool) == (1, 3)
    assert (await reservation(pool, old))["status"] == "expired"
    assert (await reservation(pool, fresh))["status"] == "reserved"
    assert (await reservation(pool, done))["status"] == "committed"
    assert await ledger.expire(pool) == 0
    await ledger.commit(pool, old, 4)  # too late: the reservation is gone
    await ledger.release(pool, old)
    assert await usage(pool) == (1, 3)
    assert await ledger.reserve(pool, "c", "credits", 6, uuid4()) is not None  # 10 - 1 - 3 left


# --- period arithmetic (SQL function, pure) -----------------------------------------------------------------


async def period_start(pool: DbPool, period: str, anchor: int | None, at: str) -> datetime:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select public.farm_period_start(%s, %s, %s::timestamptz)", (period, anchor, at)
        )
        row = await cur.fetchone()
    assert row is not None
    return row[0]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("anchor", "at", "expected"),
    [
        # anchor 31 clamps to the month length (Feb 2026 has 28 days)
        (31, "2026-02-20 12:00+00", utc(2026, 1, 31)),
        (31, "2026-02-27 23:59:59+00", utc(2026, 1, 31)),
        (31, "2026-02-28 00:00:00+00", utc(2026, 2, 28)),
        (31, "2026-03-01 00:00:00+00", utc(2026, 2, 28)),
        (31, "2026-03-30 23:00:00+00", utc(2026, 2, 28)),
        (31, "2026-03-31 00:00:00+00", utc(2026, 3, 31)),
        (31, "2026-05-01 00:00:00+00", utc(2026, 4, 30)),  # April has 30 days
        (31, "2026-01-15 00:00:00+00", utc(2025, 12, 31)),  # previous year
        # leap year: Feb 2028 has 29 days
        (31, "2028-02-28 12:00+00", utc(2028, 1, 31)),
        (31, "2028-02-29 00:00+00", utc(2028, 2, 29)),
        (31, "2028-03-01 00:00+00", utc(2028, 2, 29)),
        # ordinary anchors
        (15, "2026-03-14 23:59+00", utc(2026, 2, 15)),
        (15, "2026-03-15 00:00+00", utc(2026, 3, 15)),
        (15, "2026-12-31 00:00+00", utc(2026, 12, 15)),
        (1, "2026-07-19 08:00+00", utc(2026, 7, 1)),
        (None, "2026-07-19 08:00+00", utc(2026, 7, 1)),
        (0, "2026-07-19 08:00+00", utc(2026, 7, 1)),
    ],
)
async def test_monthly_anchor_boundaries(
    pool: DbPool, anchor: int | None, at: str, expected: datetime
) -> None:
    assert await period_start(pool, "month", anchor, at) == expected


@pytest.mark.parametrize(
    ("period", "at", "expected"),
    [
        ("minute", "2026-10-04 13:37:42+00", utc(2026, 10, 4, 13, 37)),
        ("hour", "2026-10-04 13:37:42+00", utc(2026, 10, 4, 13)),
        ("day", "2026-10-04 13:37:42+00", utc(2026, 10, 4)),
        ("day", "2026-10-04 01:00:00+05", utc(2026, 10, 3)),  # 20:00 UTC the day before
        ("week", "2026-10-04 13:37:42+00", utc(2026, 9, 28)),  # Sunday -> Monday before
        ("week", "2026-10-05 00:00:00+00", utc(2026, 10, 5)),
        ("rolling_5h", "2026-10-04 13:37:42+00", utc(1970, 1, 1)),
        ("total", "2031-01-01 00:00:00+00", utc(1970, 1, 1)),
        ("none", "2026-10-04 13:37:42+00", utc(1970, 1, 1)),
    ],
)
async def test_period_start_for_fixed_periods(pool: DbPool, period: str, at: str, expected: datetime) -> None:
    assert await period_start(pool, period, None, at) == expected


async def test_period_start_ignores_the_session_time_zone(pool: DbPool) -> None:
    for offset in ("14:00", "-14:00"):  # one of them is east of UTC, where the local date differs
        async with pool.connection() as conn:
            await conn.execute(f"set time zone interval '{offset}' hour to minute")
            cur = await conn.execute(
                "select public.farm_period_start('day', null, '2026-03-01 23:00+00'), "
                "public.farm_period_start('month', 31, '2026-03-01 23:00+00')"
            )
            row = await cur.fetchone()
            await conn.execute("reset time zone")
        assert row == (utc(2026, 3, 1), utc(2026, 2, 28)), offset


async def test_period_start_rejects_unknown_periods(pool: DbPool) -> None:
    with pytest.raises(errors.InvalidParameterValue, match="unknown period fortnight"):
        await period_start(pool, "fortnight", None, "2026-01-01 00:00+00")
