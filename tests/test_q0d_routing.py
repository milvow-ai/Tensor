"""Q0-05, Q0-07, Q0-08, Q0-12, Q0-22: scheduler resets, cross-process single-flight, ranking, cache rule.

Real Postgres. Each test reproduces the failure the audit found (`docs/audits/Q0-audit.md`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ErrorKind
from farm.manager.scheduler import reactivate_due_connections
from farm.registry import Registry
from farm.resources import flight, health, ledger
from farm.resources.ranking import rank_candidates
from farm.resources.router import UnitConfig, _load_candidates, remaining_fraction, route
from farm.resources.strategies import order_candidates
from tests.conftest import START, FakeClock, seed_connection
from tests.farm_helpers import ScriptedExecutor, fetch, ok_result, only_reoon_01

type Make = Callable[..., Awaitable[FarmContext]]

NOW = START


# --- Q0-05: the scheduler resumes on the provider's cooldown, not on a stale next_reset_at ------------------------


async def exhausted_with_stale_reset(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 5, "period": "total"}}, status="active")
    async with pool.connection() as conn:  # left behind by an earlier exhaustion that nothing ever cleared
        await conn.execute(
            "update public.consumption_units set next_reset_at = %s where connection_id = 'c'",
            (NOW - timedelta(days=30),),
        )
    await health.record_failure(pool, "c", ErrorKind.LIMIT_REACHED, "limit", now=NOW, reset_at=NOW + timedelta(hours=3))


async def test_scheduler_waits_for_the_cooldown_even_when_next_reset_at_is_stale(pool: DbPool) -> None:
    await exhausted_with_stale_reset(pool)
    assert await reactivate_due_connections(pool, NOW + timedelta(minutes=5)) == []
    assert await reactivate_due_connections(pool, NOW + timedelta(hours=2, minutes=59)) == []
    assert (await fetch(pool, "select status from connections where id = 'c'"))[0][0] == "exhausted"


async def test_scheduler_resumes_after_the_cooldown_and_clears_the_stale_reset(pool: DbPool) -> None:
    await exhausted_with_stale_reset(pool)
    back = await reactivate_due_connections(pool, NOW + timedelta(hours=3, minutes=1))
    assert back == ["c"]
    assert (await fetch(pool, "select status from connections where id = 'c'"))[0][0] == "active"
    assert await fetch(pool, "select next_reset_at from consumption_units where connection_id = 'c'") == [(None,)]


async def test_a_second_exhaustion_is_not_undone_by_the_first_one_s_reset_date(pool: DbPool) -> None:
    """The flapping of the audit: exhaust, resume, exhaust again with a later reset, scheduler tick."""
    await exhausted_with_stale_reset(pool)
    later = NOW + timedelta(hours=3, minutes=1)
    assert await reactivate_due_connections(pool, later) == ["c"]
    await health.record_failure(pool, "c", ErrorKind.LIMIT_REACHED, "limit", now=later, reset_at=later + timedelta(hours=5))
    assert await reactivate_due_connections(pool, later + timedelta(minutes=5)) == []


async def test_a_past_next_reset_at_no_longer_ranks_as_the_soonest_reset(pool: DbPool) -> None:
    await seed_connection(pool, "p", "stale", {"credits": 10}, priority=2)
    await seed_connection(pool, "p", "fresh", {"credits": 10}, priority=1)
    async with pool.connection() as conn:
        await conn.execute(
            "update public.consumption_units set next_reset_at = %s where connection_id = 'stale'",
            (NOW - timedelta(days=30),),
        )
    candidates = await _load_candidates(pool, "p", NOW)
    assert [c.id for c in rank_candidates(candidates).candidates] == ["fresh", "stale"]


# --- Q0-07: a waiter gets the leader's outcome even when it is not cached -----------------------------------------


async def _two_runs(pool: DbPool) -> tuple[object, object]:
    run1, run2 = uuid4(), uuid4()
    async with pool.connection() as conn:
        for rid in (run1, run2):
            await conn.execute(
                "insert into public.runs (id, capability, caller, status) values (%s, 'cap', 'test', 'running')",
                (rid,),
            )
        await conn.execute("insert into public.capabilities (name, kind) values ('cap', 'tool') on conflict do nothing")
    return run1, run2


COMMON = {"request_hash": "h1", "capability": "cap", "params": {}, "lease_s": 60.0, "poll_s": 0.02}


async def test_waiters_join_a_leader_whose_answer_is_not_cached(pool: DbPool) -> None:
    clock = FakeClock()
    run1, run2 = await _two_runs(pool)
    leader = await flight.claim(pool, clock, run_id=run1, **COMMON)  # type: ignore[arg-type]
    assert isinstance(leader, flight.Leader)
    waiter = asyncio.create_task(flight.claim(pool, clock, run_id=run2, **COMMON))  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    # what the router publishes for an answer that is not cached: expires_at == now
    stored = flight.StoredResult(data={"status": "unknown"}, provider="p", connection_id="c", found=False)
    assert await flight.finish_success(pool, leader.request_id, run1, stored, clock())  # type: ignore[arg-type]

    outcome = await asyncio.wait_for(waiter, 5)

    assert isinstance(outcome, flight.Joined), f"waiter became {type(outcome).__name__}: it runs the call again"
    assert outcome.stored is not None and outcome.stored.data == {"status": "unknown"}


async def test_a_waiter_that_comes_too_late_for_the_grace_period_does_not_join(pool: DbPool) -> None:
    clock = FakeClock()
    run1, run2 = await _two_runs(pool)
    leader = await flight.claim(pool, clock, run_id=run1, **COMMON)  # type: ignore[arg-type]
    assert isinstance(leader, flight.Leader)
    waiter = asyncio.create_task(flight.claim(pool, clock, run_id=run2, **COMMON))  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    stored = flight.StoredResult(data={}, provider="p", connection_id="c", found=False)
    clock.advance(flight.JOIN_GRACE_S + 1)  # the waiter was stalled for longer than the grace period
    assert await flight.finish_success(pool, leader.request_id, run1, stored, clock() - timedelta(seconds=60))  # type: ignore[arg-type]

    outcome = await asyncio.wait_for(waiter, 5)

    assert isinstance(outcome, flight.Leader)


async def test_a_plain_reader_never_sees_an_uncached_answer_as_a_cache_hit(pool: DbPool) -> None:
    clock = FakeClock()
    run1, run2 = await _two_runs(pool)
    leader = await flight.claim(pool, clock, run_id=run1, **COMMON)  # type: ignore[arg-type]
    assert isinstance(leader, flight.Leader)
    stored = flight.StoredResult(data={"x": 1}, provider="p", connection_id="c", found=False)
    await flight.finish_success(pool, leader.request_id, run1, stored, clock())  # type: ignore[arg-type]

    assert await flight.lookup_cache(pool, "h1", clock()) is None
    again = await flight.claim(pool, clock, run_id=run2, **COMMON)  # type: ignore[arg-type]
    assert isinstance(again, flight.Leader)


async def test_a_withheld_answer_cannot_be_joined(pool: DbPool) -> None:
    """A capability that is never cached keeps no answer in the database; the waiter runs the call itself."""
    clock = FakeClock()
    run1, run2 = await _two_runs(pool)
    leader = await flight.claim(pool, clock, run_id=run1, **COMMON)  # type: ignore[arg-type]
    assert isinstance(leader, flight.Leader)
    waiter = asyncio.create_task(flight.claim(pool, clock, run_id=run2, **COMMON))  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    stored = flight.StoredResult(data={}, provider="p", connection_id="c", found=True, withheld=True)
    await flight.finish_success(pool, leader.request_id, run1, stored, clock())  # type: ignore[arg-type]

    assert isinstance(await asyncio.wait_for(waiter, 5), flight.Leader)


# --- Q0-08: failover follows priority; success history only breaks ties -------------------------------------------


def _ranked(*candidates: object) -> list[str]:
    return [c.id for c in rank_candidates(list(candidates)).candidates]  # type: ignore[attr-defined]


def test_failover_follows_priority_not_the_success_rate() -> None:
    a = SimpleNamespace(id="a", priority=1, units=(), success_count=49, failure_count=1)
    b = SimpleNamespace(id="b", priority=2, units=(), success_count=0, failure_count=0)
    assert _ranked(a, b) == ["a", "b"]


def test_the_success_rate_breaks_a_tie_between_equal_priorities() -> None:
    a = SimpleNamespace(id="a", priority=1, units=(), success_count=40, failure_count=10)
    b = SimpleNamespace(id="b", priority=1, units=(), success_count=50, failure_count=0)
    assert _ranked(a, b) == ["b", "a"]
    c = SimpleNamespace(id="c", priority=1, units=(), success_count=0, failure_count=0)
    assert _ranked(a, c) == ["c", "a"]  # a new account counts as 100%


async def test_the_success_rate_is_over_the_last_50_outcomes_not_the_lifetime(pool: DbPool) -> None:
    await seed_connection(pool, "p", "old", {"credits": 10}, priority=1)
    for _ in range(60):
        await health.record_failure(pool, "old", ErrorKind.RATE_LIMITED, "429", now=NOW, retry_after_s=0)
    for _ in range(50):
        await health.record_success(pool, "old", now=NOW)

    (candidate,) = await _load_candidates(pool, "p", NOW)

    assert (candidate.success_count, candidate.failure_count) == (50, 0)  # the 60 old failures aged out
    assert (await fetch(pool, "select success_count, failure_count from connection_health"))[0] == (50, 60)


async def test_an_old_failure_does_not_demote_the_priority_one_account(pool: DbPool) -> None:
    await seed_connection(pool, "p", "first", {"credits": 10}, priority=1)
    await seed_connection(pool, "p", "second", {"credits": 10}, priority=2)
    await health.record_failure(pool, "first", ErrorKind.RATE_LIMITED, "429", now=NOW, retry_after_s=0)
    for _ in range(30):
        await health.record_success(pool, "first", now=NOW)
    candidates = await _load_candidates(pool, "p", NOW)
    assert [c.id for c in order_candidates(candidates, "failover")] == ["first", "second"]


async def test_neutral_outcomes_do_not_enter_the_window(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    await health.record_success(pool, "c", now=NOW)
    await health.record_failure(pool, "c", ErrorKind.BAD_REQUEST, "bad", now=NOW)
    assert (await fetch(pool, "select recent_outcomes from connection_health"))[0] == ("S",)


# --- Q0-12: most_remaining ranks by what is really left -----------------------------------------------------------


async def test_most_remaining_prefers_the_account_with_more_quota_left(pool: DbPool) -> None:
    await seed_connection(pool, "p", "a", {"credits": {"limit": 10, "period": "day"}}, priority=1)
    await seed_connection(pool, "p", "b", {"credits": {"limit": 10, "period": "day"}}, priority=2)
    res = await ledger.reserve(pool, "a", "credits", 9, uuid4())
    assert res is not None
    await ledger.commit(pool, res, 9)  # a has 10% left, b 100%

    candidates = await _load_candidates(pool, "p")

    by_id = {c.id: c for c in candidates}
    assert round(by_id["a"].remaining_fraction, 2) == 0.1 and by_id["b"].remaining_fraction == 1.0
    assert [c.id for c in order_candidates(candidates, "most_remaining")] == ["b", "a"]


async def test_remaining_fraction_counts_open_reservations_and_the_tightest_unit() -> None:
    def unit(name: str, limit: int | None, consumed: int) -> UnitConfig:
        from decimal import Decimal

        return UnitConfig(
            unit=name,
            limit=None if limit is None else Decimal(limit),
            period="day",
            charged_on="attempt",
            unit_cost_usd=Decimal(0),
            estimate=Decimal(1),
            consumed=Decimal(consumed),
        )

    assert remaining_fraction([]) == 1.0
    assert remaining_fraction([unit("a", None, 99)]) == 1.0  # unlimited
    assert remaining_fraction([unit("a", 10, 5), unit("b", 100, 90)]) == 0.1  # the tightest unit counts
    assert remaining_fraction([unit("a", 10, 15)]) == 0.0


async def test_the_router_sends_the_call_to_the_account_with_most_quota_left(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.providers["reoon"].default_strategy = "most_remaining"
    executor = ScriptedExecutor(ok_result())
    ctx = await farm_factory(registry, executors={"api": executor})
    res = await ledger.reserve(pool, "reoon-01", "credits", 19, uuid4())  # reoon-01 (priority 1) has 5% left
    assert res is not None
    await ledger.commit(pool, res, 19)

    out = await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", strategy="most_remaining")

    assert out.ok and out.source is not None and out.source.connection_id == "reoon-02"


# --- Q0-22: only found = true is cached ---------------------------------------------------------------------------


async def test_an_answer_with_found_none_is_not_cached(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    executor = ScriptedExecutor(ok_result(found=None))
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    for _ in range(2):
        out = await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01")
        assert out.ok and out.source is not None and not out.source.cached
    assert len(executor.calls) == 2


async def test_an_answer_with_found_true_is_cached(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    executor = ScriptedExecutor(ok_result(found=True))
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    first = await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test")
    second = await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test")
    assert first.ok and second.source is not None and second.source.cached
    assert len(executor.calls) == 1


async def test_an_answer_with_found_false_is_not_cached(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    executor = ScriptedExecutor(ok_result(found=False))
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test")
    again = await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test")
    assert again.source is not None and not again.source.cached
    assert len(executor.calls) == 2

