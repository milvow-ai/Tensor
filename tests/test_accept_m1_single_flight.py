"""M1 acceptance: identical requests are executed once, in one process and across processes.

The race is real: ten callers start together while the provider is still thinking. Without single-flight each
would reserve quota and call the provider (ten credits for one answer).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import uuid4

import httpx
import respx

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ErrorKind
from farm.registry import Registry
from farm.resources.router import RouteOutcome, request_hash, route
from tests.conftest import REOON_URL, START, FakeClock, reoon_body
from tests.farm_helpers import (
    EMAIL,
    ScriptedExecutor,
    events,
    failure,
    fetch,
    kinds,
    ok_result,
    only_reoon_01,
    quota,
    reservations,
    run_row,
)

type Make = Callable[..., Awaitable[FarmContext]]


async def test_ten_concurrent_identical_calls_make_one_provider_call_and_one_charge(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))

    async def slow_reoon(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)  # long enough for all ten callers to be waiting on it
        return httpx.Response(200, json=reoon_body("safe"))

    reoon = http.get(REOON_URL).mock(side_effect=slow_reoon)

    outcomes = await asyncio.gather(
        *(route(ctx, "verify_email", {"email": EMAIL}, caller="test") for _ in range(10))
    )

    assert reoon.call_count == 1  # one request to the provider for ten callers
    assert all(o.ok for o in outcomes)
    assert len({str(o.result) for o in outcomes}) == 1  # ten identical answers
    assert len({o.run_id for o in outcomes}) == 10  # every caller still has its own run
    assert [r[2] for r in await reservations(pool, "reoon-01")] == [
        "committed"
    ]  # one reservation, one charge
    assert await quota(pool, "reoon-01") == (1, 0)

    joined = [o for o in outcomes if "single_flight_join" in {k for k, _, _ in await events(pool, o.run_id)}]
    assert len(joined) == 9
    led = [o for o in outcomes if o not in joined]
    assert len(led) == 1 and led[0].cost.units == {"credits": 1.0}
    assert all(o.cost.units == {} and o.cost.usd == 0 for o in joined)  # only the leader paid
    for o in joined:  # a joiner knows who answered it
        assert o.source is not None and o.source.connection_id == "reoon-01"
        assert (await run_row(pool, o.run_id))["status"] == "succeeded"
    assert ctx.flights == {}  # nothing left registered


async def test_ten_concurrent_calls_when_the_provider_fails_all_get_the_one_failure(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor(failure(ErrorKind.SERVER, "provider down"), delay_s=0.2)
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})

    outcomes = await asyncio.gather(
        *(route(ctx, "verify_email", {"email": EMAIL}, caller="test") for _ in range(5))
    )

    assert len(executor.calls) == 2  # reoon-01 then zerobounce-01, once, not once per caller
    assert all(not o.ok and o.error is not None and o.error.kind == "server" for o in outcomes)
    assert await quota(pool, "reoon-01") == (0, 0)


async def test_a_leader_whose_caller_is_cancelled_still_finishes_and_settles(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor(ok_result(), delay_s=0.2)
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})

    leader = asyncio.create_task(route(ctx, "verify_email", {"email": EMAIL}, caller="test"))
    await executor.started.wait()
    joiner = asyncio.create_task(route(ctx, "verify_email", {"email": EMAIL}, caller="test"))
    await asyncio.sleep(0.05)
    leader.cancel()  # the client that started the call goes away mid-flight

    answer = await joiner  # the joiner is served by the execution that kept running
    assert answer.ok and len(executor.calls) == 1
    assert [r[2] for r in await reservations(pool, "reoon-01")] == ["committed"]
    assert await quota(pool, "reoon-01") == (1, 0)
    rows = await fetch(pool, "select status from runs order by started_at")
    assert [r[0] for r in rows] == ["succeeded", "succeeded"]  # no run left 'running'


async def test_a_request_running_in_another_process_is_waited_for_not_repeated(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock)
    reoon = http.get(REOON_URL).respond(200, json=reoon_body())
    h = request_hash("verify_email", {"email": EMAIL})
    other_run = uuid4()
    # another Farm process claimed the request and is executing it right now
    await fetch(
        pool,
        "insert into capability_requests (request_hash, capability, params, status, run_id, expires_at) "
        "values (%s, 'verify_email', %s, 'running', %s, %s) returning 1",
        h,
        '{"email": "jane.doe@example.com"}',
        other_run,
        START + timedelta(seconds=300),
    )

    waiting = asyncio.create_task(route(ctx, "verify_email", {"email": EMAIL}, caller="test"))
    await asyncio.sleep(0.15)
    assert not waiting.done()  # it is waiting, not executing
    await fetch(
        pool,
        "update capability_requests set status = 'succeeded', expires_at = %s, result = %s where request_hash = %s returning 1",
        START + timedelta(days=1),
        '{"data": {"email": "jane.doe@example.com", "status": "valid", "sub_status": null, "provider": "zerobounce", '
        '"checked_at": "2026-10-04T12:00:00Z"}, "provider": "zerobounce", "connection_id": "zerobounce-01", "found": true}',
        h,
    )

    out = await asyncio.wait_for(waiting, 5)

    assert out.ok and out.result is not None and out.result["provider"] == "zerobounce"
    assert reoon.call_count == 0 and await reservations(pool) == []  # this process spent nothing
    join = [d for k, _, d in await events(pool, out.run_id) if k == "single_flight_join"]
    assert join == [{"scope": "cross_process", "request_hash": h, "leader_run_id": str(other_run)}]


async def test_a_leader_that_died_is_taken_over_when_its_lease_runs_out(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock)
    reoon = http.get(REOON_URL).respond(200, json=reoon_body())
    h = request_hash("verify_email", {"email": EMAIL})
    # a crashed process left its 'running' row behind; its lease ends in 60 s
    await fetch(
        pool,
        "insert into capability_requests (request_hash, capability, params, status, run_id, expires_at) "
        "values (%s, 'verify_email', '{}', 'running', %s, %s) returning 1",
        h,
        uuid4(),
        START + timedelta(seconds=60),
    )

    waiting = asyncio.create_task(route(ctx, "verify_email", {"email": EMAIL}, caller="test"))
    await asyncio.sleep(0.15)
    assert not waiting.done() and reoon.call_count == 0  # the lease is still valid: no stealing
    clock.advance(61)

    out = await asyncio.wait_for(waiting, 5)

    assert out.ok and reoon.call_count == 1  # taken over and executed here
    assert [r[0] for r in await fetch(pool, "select status from capability_requests")] == ["succeeded"]


async def test_two_takeovers_of_the_same_dead_lease_have_exactly_one_winner(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor(ok_result(), delay_s=0.2)
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock, executors={"api": executor})
    other = await farm_factory(None, clock=clock, executors={"api": executor})  # a second Farm process
    h = request_hash("verify_email", {"email": EMAIL})
    await fetch(
        pool,
        "insert into capability_requests (request_hash, capability, params, status, run_id, expires_at) "
        "values (%s, 'verify_email', '{}', 'running', %s, %s) returning 1",
        h,
        uuid4(),
        START - timedelta(seconds=1),  # already expired
    )

    outcomes: list[RouteOutcome] = await asyncio.gather(
        route(ctx, "verify_email", {"email": EMAIL}, caller="test"),
        route(other, "verify_email", {"email": EMAIL}, caller="test"),
    )

    assert all(o.ok for o in outcomes)
    assert len(executor.calls) == 1  # one executed, the other waited for it across the 'process' boundary
    assert [r[2] for r in await reservations(pool, "reoon-01")] == ["committed"]
    assert sorted(o.cost.units == {"credits": 1.0} for o in outcomes) == [False, True]
    assert (
        len([k for o in outcomes for k, _, _ in await events(pool, o.run_id) if k == "single_flight_join"])
        == 1
    )
    assert ("execute", "reoon-01") in kinds(
        await events(pool, next(o.run_id for o in outcomes if o.cost.units))
    )
