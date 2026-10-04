"""M1 acceptance: the router fails over like the owner needs it to, with real Postgres and mocked providers.

Each test reproduces a failure the Farm must survive: an exhausted account, a provider that errors, a rate
limit, and a route where everything is down. Providers are mocked at the HTTP layer (respx), so the real
adapters, executor, router, ledger, health and trajectory code all run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal

import httpx
import respx

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.registry import Registry
from farm.resources.router import route
from tests.conftest import REOON_URL, START, ZEROBOUNCE_URL, FakeClock, reoon_body, zerobounce_body
from tests.farm_helpers import (
    EMAIL,
    events,
    fetch,
    health,
    kinds,
    only_reoon_01,
    quota,
    reservations,
    run_row,
    status_of,
)

type Make = Callable[..., Awaitable[FarmContext]]


async def test_exhausted_reoon_fails_over_to_zerobounce_without_touching_reoon(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(200, json=reoon_body())
    zerobounce = http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))
    # Reoon's 20 daily credits are all used up (as the ledger would have recorded them).
    await fetch(
        pool,
        "insert into quota_usage (connection_id, unit, period_start, used, reserved, limit_value) "
        "values ('reoon-01', 'credits', farm_period_start('day', null, now()), 20, 0, 20) returning 1",
    )

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.result is not None and out.result["provider"] == "zerobounce"
    assert out.source is not None and (out.source.provider, out.source.connection_id) == (
        "zerobounce",
        "zerobounce-01",
    )
    assert not out.source.cached and out.cost.units == {"credits": 1.0}
    assert reoon.call_count == 0 and zerobounce.call_count == 1
    assert await reservations(pool, "reoon-01") == []  # nothing was ever held on the exhausted account
    assert await reservations(pool, "zerobounce-01") == [
        ("zerobounce-01", "credits", "committed", Decimal(1))
    ]
    assert await quota(pool, "zerobounce-01") == (1, 0)

    trail = kinds(await events(pool, out.run_id))
    assert trail.index(("reserve_failed", "reoon-01")) < trail.index(("success", "zerobounce-01"))
    assert ("execute", "reoon-01") not in trail
    run = await run_row(pool, out.run_id)
    assert (run["status"], run["connection_id"], run["finished"]) == ("succeeded", "zerobounce-01", True)


async def test_provider_error_releases_the_reservation_records_health_and_falls_back(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(500, json={"status": "error", "reason": "internal error"})
    zerobounce = http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.provider == "zerobounce"
    assert reoon.call_count == 1 and zerobounce.call_count == 1  # exactly one call each
    # Reoon charges on success: the failed attempt consumed nothing, so its reservation was released.
    assert await reservations(pool, "reoon-01") == [("reoon-01", "credits", "released", None)]
    assert await quota(pool, "reoon-01") == (0, 0)
    assert await reservations(pool, "zerobounce-01") == [
        ("zerobounce-01", "credits", "committed", Decimal(1))
    ]

    h = await health(pool, "reoon-01")
    assert (h["failure_count"], h["consecutive_failures"], h["last_error_kind"], h["circuit"]) == (
        1,
        1,
        "server",
        "closed",
    )
    assert (await health(pool, "zerobounce-01"))["success_count"] == 1

    trail = kinds(await events(pool, out.run_id))
    assert (
        trail.index(("failure", "reoon-01"))
        < trail.index(("fallback", "reoon-01"))
        < trail.index(("success", "zerobounce-01"))
    )
    assert ("release", "reoon-01") in trail


async def test_rate_limit_puts_the_account_on_cooldown_and_the_next_request_skips_it(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock)
    reoon = http.get(REOON_URL).respond(
        429, headers={"Retry-After": "120"}, json={"status": "error", "reason": "rate limit"}
    )
    zerobounce = http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    first = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    assert first.ok and reoon.call_count == 1
    assert (await health(pool, "reoon-01"))["cooldown_until"] == START + timedelta(seconds=120)

    second = await route(ctx, "verify_email", {"email": "someone.else@example.com"}, caller="test")

    assert second.ok and reoon.call_count == 1  # not even tried
    skips = [(c, d["reason"]) for k, c, d in await events(pool, second.run_id) if k == "skip"]
    assert skips == [("reoon-01", "cooldown")]
    assert zerobounce.call_count == 2

    clock.advance(121)  # the cooldown is over: Reoon is asked again
    third = await route(ctx, "verify_email", {"email": "third@example.com"}, caller="test")
    assert reoon.call_count == 2 and third.ok


async def test_all_pools_failing_returns_the_last_error_and_leaves_no_reservation_held(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    http.get(REOON_URL).respond(500, json={"status": "error", "reason": "internal error"})
    http.get(ZEROBOUNCE_URL).respond(429, headers={"Retry-After": "30"}, json={"error": "slow down"})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not out.ok and out.result is None and out.error is not None
    assert out.error.kind == "rate_limited"  # the kind of the last attempt, not the first
    assert out.error.retry_after_s == 30 and out.error.hint
    assert [(a.provider, a.connection_id, a.outcome, a.kind) for a in out.error.attempts] == [
        ("reoon", "reoon-01", "failed", "server"),
        ("zerobounce", "zerobounce-01", "failed", "rate_limited"),
    ]
    assert out.source is not None and out.source.connection_id == "zerobounce-01"
    assert [r[2] for r in await reservations(pool)] == ["released", "released"]  # nothing left 'reserved'
    assert await quota(pool, "reoon-01") == (0, 0) and await quota(pool, "zerobounce-01") == (0, 0)
    run = await run_row(pool, out.run_id)
    assert (run["status"], run["error_kind"], run["finished"]) == ("failed", "rate_limited", True)


async def test_auth_failure_marks_the_account_needs_login_and_it_is_skipped_afterwards(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(401, json={"status": "error", "reason": "invalid api key"})
    http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    first = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    second = await route(ctx, "verify_email", {"email": "other@example.com"}, caller="test")

    assert first.ok and second.ok and reoon.call_count == 1
    assert await status_of(pool, "reoon-01") == "needs_login"
    skips = [(c, d["reason"]) for k, c, d in await events(pool, second.run_id) if k == "skip"]
    assert skips == [("reoon-01", "needs_login")]


async def test_a_reoon_unknown_verdict_is_answered_but_not_charged(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    http.get(REOON_URL).respond(200, json=reoon_body("unknown"))

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.result is not None and out.result["status"] == "unknown"
    assert out.cost.units == {} and out.cost.usd == 0
    assert await reservations(pool, "reoon-01") == [
        ("reoon-01", "credits", "released", None)
    ]  # Reoon refunds unknown


async def test_a_transport_failure_is_a_server_error_and_falls_back(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    http.get(REOON_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.provider == "zerobounce"
    assert (await health(pool, "reoon-01"))["last_error_kind"] == "server"
