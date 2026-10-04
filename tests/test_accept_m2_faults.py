"""M2 acceptance: resilience against provider faults (429, 401, empty, timeout, 500, malformed) and circuit breaker lifecycle.

Real Postgres, mocked HTTP layer (respx), verifying ErrorKind, health transitions,
fallback to second pool, reservation hygiene (none left reserved), and 0 real network calls.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx
import pytest
import respx

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ErrorKind, ExecResult
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
    status_of,
)

type Make = Callable[..., Awaitable[FarmContext]]

HUNTER_URL = "https://api.hunter.io/v2/email-finder"
APOLLO_URL = "https://api.apollo.io/api/v1/people/match"


@pytest.mark.parametrize(
    ("status_code", "response_headers", "response_content", "expected_kind"),
    [
        (429, {"Retry-After": "60"}, '{"status":"error","reason":"rate limit"}', "rate_limited"),
        (401, {}, '{"status":"error","reason":"invalid key"}', "auth"),
        (500, {}, '{"status":"error","reason":"internal crash"}', "server"),
    ],
)
async def test_fault_http_errors_fallback_and_keep_clean_reservations(
    pool: DbPool,
    farm_factory: Make,
    registry: Registry,
    http: respx.MockRouter,
    status_code: int,
    response_headers: dict[str, str],
    response_content: str,
    expected_kind: str,
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(
        status_code, headers=response_headers, text=response_content
    )
    zerobounce = http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.provider == "zerobounce"
    assert reoon.call_count == 1
    assert zerobounce.call_count == 1

    # Reservation hygiene: Reoon released, Zerobounce committed; 0 left 'reserved'
    reoon_res = await reservations(pool, "reoon-01")
    zb_res = await reservations(pool, "zerobounce-01")
    assert all(r[2] in ("committed", "released") for r in reoon_res + zb_res)
    assert not any(r[2] == "reserved" for r in reoon_res + zb_res)
    assert await quota(pool, "reoon-01") == (0, 0)
    assert await quota(pool, "zerobounce-01") == (1, 0)

    # Health transition on Reoon
    h = await health(pool, "reoon-01")
    if expected_kind == "auth":
        assert await status_of(pool, "reoon-01") == "needs_login"
    elif expected_kind == "rate_limited":
        assert h["last_error_kind"] == "rate_limited"
        assert h["cooldown_until"] is not None
    elif expected_kind == "server":
        assert h["last_error_kind"] == "server"
        assert h["consecutive_failures"] == 1


async def test_fault_timeout_falls_back_to_second_pool(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).mock(side_effect=httpx.ReadTimeout("timeout from reoon"))
    zerobounce = http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.provider == "zerobounce"
    assert reoon.call_count == 1 and zerobounce.call_count == 1

    # Reoon released, nothing left reserved
    assert [r[2] for r in await reservations(pool, "reoon-01")] == ["released"]
    assert (await health(pool, "reoon-01"))["last_error_kind"] in ("timeout", "server")


async def test_fault_malformed_response_falls_back_to_second_pool(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(200, text="<!DOCTYPE html><html>502 Bad Gateway</html>")
    zerobounce = http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.provider == "zerobounce"
    assert reoon.call_count == 1 and zerobounce.call_count == 1

    # Reoon released, nothing left reserved
    assert [r[2] for r in await reservations(pool, "reoon-01")] == ["released"]
    h = await health(pool, "reoon-01")
    assert h["last_error_kind"] in ("server", "unknown")


async def test_fault_empty_answer_falls_back_on_find_capability(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    from farm.registry import CapabilitySpec
    from tests.farm_helpers import ScriptedExecutor

    registry.capabilities["find_widget"] = CapabilitySpec(
        kind="tool", routes=["reoon", "zerobounce"]
    )
    executor = ScriptedExecutor(
        lambda r: ExecResult(
            ok=True, data={"widget": None}, found=False, error_kind=ErrorKind.EMPTY, error="not found"
        )
        if r.connection.id == "reoon-01"
        else ExecResult(ok=True, data={"widget": "zb_found"}, found=True, units_used={"credits": 1})
    )
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})

    out = await route(ctx, "find_widget", {"q": 1}, caller="test")

    assert out.ok and out.source is not None and out.source.provider == "zerobounce"
    assert [c.connection.id for c in executor.calls] == ["reoon-01", "zerobounce-01"]

    # Reoon gave an empty answer, which is not an account failure (no health row written)
    assert await fetch(pool, "select 1 from connection_health where connection_id = 'reoon-01'") == []


async def test_circuit_breaker_full_lifecycle(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock)

    # Reoon fails with 500, Zerobounce answers
    reoon = http.get(REOON_URL).respond(500, json={"status": "error", "reason": "server down"})
    zerobounce = http.get(ZEROBOUNCE_URL).respond(200, json=zerobounce_body("valid"))

    # 5 consecutive 500s open the circuit
    for i in range(5):
        out = await route(ctx, "verify_email", {"email": f"user{i}@example.com"}, caller="test")
        assert out.ok and out.source is not None and out.source.provider == "zerobounce"

    h = await health(pool, "reoon-01")
    assert h["circuit"] == "open" and h["consecutive_failures"] == 5
    assert h["cooldown_until"] == START + timedelta(seconds=120)
    assert reoon.call_count == 5

    # Request during open circuit: skips Reoon completely without calling it
    skipped_out = await route(ctx, "verify_email", {"email": "during_open@example.com"}, caller="test")
    assert skipped_out.ok and skipped_out.source is not None and skipped_out.source.provider == "zerobounce"
    assert reoon.call_count == 5  # No additional calls to Reoon
    trail = kinds(await events(pool, skipped_out.run_id))
    assert ("skip", "reoon-01") in trail

    # Advance clock past open_seconds (120s)
    clock.advance(125)

    # Reoon recovers
    http.get(REOON_URL).respond(200, json=reoon_body("safe"))

    # Next request claims exactly one half-open probe on Reoon
    probe_out = await route(ctx, "verify_email", {"email": "probe@example.com"}, caller="test")
    assert probe_out.ok and probe_out.source is not None and probe_out.source.connection_id == "reoon-01"
    assert reoon.call_count == 6

    # Successful probe closes the circuit and resets consecutive failures
    assert zerobounce.call_count == 6
    h = await health(pool, "reoon-01")
    assert h["circuit"] == "closed"
    assert h["consecutive_failures"] == 0
    assert h["cooldown_until"] is None
