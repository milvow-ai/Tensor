"""Q0-09: the Console counts only calls a connection really answered.

A run answered from the cache, or by joining a call that was already running, carries the ``connection_id`` of
the account that originally answered (``runs.connection_id``). The views counted those runs for that account.
The rows below are built by hand, one of each kind, so the expected numbers are plain arithmetic.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from farm.db.pool import DbPool
from tests.farm_helpers import fetch

PAID = '{"price_usd": 50.0, "name": "Pro"}'


async def _run(pool: DbPool, connection_id: str, *, cached: bool, event: str | None = None) -> UUID:
    run_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.runs (id, capability, caller, strategy, status, cost_usd, cached, connection_id,
                                     started_at, finished_at)
            values (%s, 'verify_email', 'test', 'failover', 'succeeded', 0, %s, %s,
                    now() - interval '1 minute', now())
            """,
            (run_id, cached, connection_id),
        )
        if event is not None:
            await conn.execute(
                "insert into public.run_events (run_id, seq, kind, data) values (%s, 1, %s, '{}'::jsonb)",
                (run_id, event),
            )
    return run_id


async def _seed(pool: DbPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.providers (id, name, kind, executor, default_strategy, enabled) "
            "values ('p_tool', 'Tool', 'tool', 'api', 'failover', true)"
        )
        await conn.execute(
            """
            insert into public.connections (id, provider_id, label, auth_ref, priority, concurrency, status, plan)
            values ('c_answered', 'p_tool', 'Answered', 'env:A', 10, 1, 'active', %s::jsonb),
                   ('c_echoed', 'p_tool', 'Only echoes', 'env:B', 20, 1, 'active', %s::jsonb)
            """,
            (PAID, PAID),
        )
        await conn.execute(
            "insert into public.usage_events (connection_id, unit, amount, kind, cost_usd) "
            "values ('c_answered', 'credits', 1, 'actual', 0.10)"
        )


async def test_cache_hits_and_joins_are_not_calls_results_or_activity(pool: DbPool) -> None:
    await _seed(pool)
    # c_answered: one real call, plus a cache hit and a single-flight join that name it as the answerer
    await _run(pool, "c_answered", cached=False)
    await _run(pool, "c_answered", cached=True, event="cache_hit")
    await _run(pool, "c_answered", cached=False, event="single_flight_join")
    # c_echoed: only ever a cache hit (flagged) and a join (not flagged) of somebody else's answer
    await _run(pool, "c_echoed", cached=True, event="cache_hit")
    await _run(pool, "c_echoed", cached=False, event="single_flight_join")

    calls = dict(await fetch(pool, "select connection_id, max(calls_today) from public.v_connection_status group by 1"))
    results = dict(
        await fetch(pool, "select connection_id, successful_results from public.v_cost_per_result")
    )
    per_result = dict(await fetch(pool, "select connection_id, cost_per_result from public.v_cost_per_result"))
    idle = {row[0] for row in await fetch(pool, "select connection_id from public.v_idle_paid")}

    assert calls == {"c_answered": 1, "c_echoed": 0}
    assert results == {"c_answered": 1, "c_echoed": 0}
    assert float(per_result["c_answered"]) == 0.10  # $0.10 for the ONE call it answered, not $0.033 for three
    assert per_result["c_echoed"] is None
    assert idle == {"c_echoed"}  # a paid account that only appears in echoes is idle; the one that answered is not


async def test_a_real_success_still_counts_everywhere(pool: DbPool) -> None:
    await _seed(pool)
    await _run(pool, "c_echoed", cached=False)

    calls = dict(await fetch(pool, "select connection_id, max(calls_today) from public.v_connection_status group by 1"))
    results = dict(
        await fetch(pool, "select connection_id, successful_results from public.v_cost_per_result")
    )
    idle = {row[0] for row in await fetch(pool, "select connection_id from public.v_idle_paid")}

    assert calls["c_echoed"] == 1 and results["c_echoed"] == 1
    assert idle == {"c_answered"}  # c_answered has no run at all here
