"""M1 acceptance: the ledger never over-reserves, however many callers race."""

import asyncio
import random
from uuid import UUID, uuid4

from farm.db.pool import DbPool, open_pool
from farm.resources import ledger
from tests.conftest import seed_connection


async def totals(pool: DbPool, conn_id: str = "c", unit: str = "credits") -> tuple[int, int]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select coalesce(sum(used), 0)::int, coalesce(sum(reserved), 0)::int from quota_usage "
            "where connection_id = %s and unit = %s",
            (conn_id, unit),
        )
        row = await cur.fetchone()
    assert row is not None
    return row[0], row[1]


async def test_200_concurrent_reserves_against_limit_50(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 50})

    results = await asyncio.gather(*(ledger.reserve(pool, "c", "credits", 1, uuid4()) for _ in range(200)))
    granted = [r for r in results if r is not None]

    assert len(granted) == 50  # exactly the limit, no more, no fewer
    assert len(set(granted)) == 50
    used, reserved = await totals(pool)
    assert used + reserved == 50

    # settle: 30 committed, 20 released
    await asyncio.gather(
        *(ledger.commit(pool, res, 1) for res in granted[:30]),
        *(ledger.release(pool, res) for res in granted[30:]),
    )
    assert await totals(pool) == (30, 0)
    async with pool.connection() as conn:
        cur = await conn.execute("select status, count(*) from quota_reservations group by status order by 1")
        assert await cur.fetchall() == [("committed", 30), ("released", 20)]

    # the 20 released units are available again, and only those
    again = await asyncio.gather(*(ledger.reserve(pool, "c", "credits", 1, uuid4()) for _ in range(40)))
    assert sum(r is not None for r in again) == 20
    used, reserved = await totals(pool)
    assert used + reserved == 50


async def test_concurrent_reserves_from_two_pools(pool: DbPool, db_url: str) -> None:
    """Separate pools (stand-ins for separate Farm processes) still share one limit."""
    await seed_connection(pool, "p", "c", {"credits": 50})
    other = await open_pool(db_url)
    try:
        results = await asyncio.gather(
            *(ledger.reserve(pool, "c", "credits", 1, uuid4()) for _ in range(100)),
            *(ledger.reserve(other, "c", "credits", 1, uuid4()) for _ in range(100)),
        )
    finally:
        await other.close()
    assert sum(r is not None for r in results) == 50
    assert await totals(pool) == (0, 50)


async def test_concurrent_duplicate_commits_and_releases_apply_once(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    a = await ledger.reserve(pool, "c", "credits", 4, uuid4())
    b = await ledger.reserve(pool, "c", "credits", 3, uuid4())
    assert a is not None and b is not None

    await asyncio.gather(
        *(ledger.commit(pool, a, 4) for _ in range(25)),
        *(ledger.release(pool, b) for _ in range(25)),
        *(ledger.commit(pool, b, 3) for _ in range(5)),  # racing a release: exactly one of them wins
    )
    used, reserved = await totals(pool)
    assert reserved == 0
    assert used in (4, 7)  # 4 from `a`, plus 3 only if a commit of `b` beat every release
    async with pool.connection() as conn:
        cur = await conn.execute("select status from quota_reservations where id = %s", (b,))
        row = await cur.fetchone()
    assert row is not None and row[0] == ("committed" if used == 7 else "released")


async def test_random_mixed_workload_keeps_the_invariants(pool: DbPool) -> None:
    """300 workers reserve, then commit (partial or zero), release or abandon; the limit is never exceeded."""
    limit = 60
    await seed_connection(pool, "p", "c", {"credits": limit})
    rng = random.Random(1234)
    max_seen = 0
    abandoned: list[UUID] = []

    async def worker(n: int) -> None:
        nonlocal max_seen
        amount = rng.choice([1, 1, 2, 3])
        res = await ledger.reserve(pool, "c", "credits", amount, uuid4())
        used, reserved = await totals(pool)
        max_seen = max(max_seen, used + reserved)
        if res is None:
            return
        action = n % 4
        if action == 0:
            await ledger.commit(pool, res, amount)
        elif action == 1:
            await ledger.commit(pool, res, rng.randint(0, amount))
        elif action == 2:
            await ledger.release(pool, res)
        else:
            abandoned.append(res)  # never settled: stays reserved until it expires

    await asyncio.gather(*(worker(n) for n in range(300)))

    assert max_seen <= limit
    used, reserved = await totals(pool)
    assert used + reserved <= limit
    async with pool.connection() as conn:
        await conn.execute(
            "update quota_reservations set expires_at = now() - interval '1 second' where status = 'reserved'"
        )
    assert await ledger.expire(pool) == len(abandoned)
    used_after, reserved_after = await totals(pool)
    assert reserved_after == 0 and used_after == used
