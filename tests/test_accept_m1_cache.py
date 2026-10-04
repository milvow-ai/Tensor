"""M1 acceptance: a verified address is answered from the cache until its time to live is over."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

import respx

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.registry import Registry
from farm.resources.router import route
from tests.conftest import REOON_URL, FakeClock, reoon_body
from tests.farm_helpers import EMAIL, events, fetch, kinds, only_reoon_01, quota, reservations, run_row

type Make = Callable[..., Awaitable[FarmContext]]

TTL_S = 5_184_000  # verify_email: 60 days in the fixture registry


async def test_second_identical_call_is_served_from_the_cache_for_free_until_the_ttl_ends(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock)
    reoon = http.get(REOON_URL).respond(200, json=reoon_body("safe"))

    first = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    clock.advance(3600)
    second = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert first.ok and second.ok and reoon.call_count == 1
    assert second.result == first.result
    assert second.source is not None and second.source.cached and second.source.connection_id == "reoon-01"
    assert second.cost.usd == 0 and second.cost.units == {}
    assert ("cache_hit", "reoon-01") in kinds(await events(pool, second.run_id))
    run = await run_row(pool, second.run_id)
    assert (run["status"], run["cached"], run["cost_usd"], run["finished"]) == ("succeeded", True, 0, True)
    assert await quota(pool, "reoon-01") == (1, 0)  # the cache hit cost no credit

    clock.advance(TTL_S)  # now past the end of the cache life: the provider is asked again
    third = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert third.ok and reoon.call_count == 2
    assert third.source is not None and not third.source.cached
    assert await quota(pool, "reoon-01") == (2, 0)


async def test_the_cache_key_is_the_normalised_request(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(200, json=reoon_body("safe"))

    await route(ctx, "verify_email", {"email": "Jane.Doe@EXAMPLE.com"}, caller="test")
    again = await route(ctx, "verify_email", {"email": "  Jane.Doe@example.com "}, caller="test")

    assert reoon.call_count == 1 and again.source is not None and again.source.cached


async def test_an_inconclusive_unknown_verdict_is_not_cached(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(200, json=reoon_body("unknown"))

    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert reoon.call_count == 2  # an 'unknown' verdict is asked again, it is not a fact worth 60 days


async def test_failures_are_not_cached(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(500, json={"status": "error", "reason": "down"})
    http.get("https://api.zerobounce.net/v2/validate").respond(500, json={"error": "down"})

    first = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    second = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not first.ok and not second.ok and reoon.call_count == 2
    assert [r[0] for r in await fetch(pool, "select status from capability_requests")] == ["failed"]


async def test_a_pinned_call_bypasses_the_cache_and_runs_on_that_connection(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(registry)
    reoon = http.get(REOON_URL).respond(200, json=reoon_body("safe"))

    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")  # fills the cache from reoon-01
    pinned = await route(ctx, "verify_email", {"email": EMAIL}, caller="test", pin="reoon-02")

    assert pinned.ok and reoon.call_count == 2
    assert pinned.source is not None and (pinned.source.connection_id, pinned.source.cached) == (
        "reoon-02",
        False,
    )
    assert [r[2] for r in await reservations(pool, "reoon-02")] == ["committed"]
    assert await quota(pool, "reoon-01") == (1, 0) and await quota(pool, "reoon-02") == (1, 0)


async def test_cache_ttl_is_taken_from_the_capability_row_not_a_constant(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    registry.capabilities["verify_email"].cache_ttl_seconds = 600
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock)
    reoon = http.get(REOON_URL).respond(200, json=reoon_body("safe"))

    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    clock.advance(599)
    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    assert reoon.call_count == 1
    clock.advance(2)
    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    assert reoon.call_count == 2
    expires = (await fetch(pool, "select expires_at from capability_requests"))[0][0]
    assert expires - clock.now == timedelta(seconds=600)
