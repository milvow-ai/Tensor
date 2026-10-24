"""Q0-01, Q0-24: the half-open circuit is a probe lease that every outcome resolves.

Each row of the state transition table in ``farm/resources/health.py`` is tested, then the router paths that must
end a probe (cancellation, an empty answer, a refusal before the call). Q0-24: ``record_failure`` for a login
failure must work on a pool that has a single connection.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from psycopg_pool import AsyncConnectionPool

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ErrorKind, ExecResult
from farm.manager.alerts import list_open_alerts
from farm.registry import Registry
from farm.resources import health
from farm.resources.router import route
from tests.conftest import START, FakeClock, seed_connection
from tests.farm_helpers import ScriptedExecutor, fetch, ok_result, only_reoon_01
from tests.farm_helpers import health as health_row

type Make = Callable[..., Awaitable[FarmContext]]

NOW = START
LATER = NOW + timedelta(seconds=125)  # the 120 s of an open circuit are over


async def open_circuit(pool: DbPool, conn_id: str = "c") -> None:
    await seed_connection(pool, "p", conn_id, {"credits": {"limit": 10, "period": "day"}})
    for _ in range(5):
        await health.record_failure(pool, conn_id, ErrorKind.SERVER, "boom", now=NOW)
    assert (await health_row(pool, conn_id))["circuit"] == "open"


# --- the state transition table ------------------------------------------------------------------------------


async def test_row_closed_to_open_on_the_nth_circuit_failure(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    for _ in range(4):
        await health.record_failure(pool, "c", ErrorKind.TIMEOUT, "slow", now=NOW)
    assert (await health_row(pool, "c"))["circuit"] == "closed"
    await health.record_failure(pool, "c", ErrorKind.TIMEOUT, "slow", now=NOW)
    h = await health_row(pool, "c")
    assert h["circuit"] == "open" and h["cooldown_until"] == NOW + timedelta(seconds=120)


async def test_row_open_cannot_be_claimed_before_its_cooldown_is_over(pool: DbPool) -> None:
    await open_circuit(pool)
    assert not await health.claim_probe(pool, "c", NOW + timedelta(seconds=119))
    assert (await health_row(pool, "c"))["circuit"] == "open"


async def test_row_open_to_half_open_takes_a_lease(pool: DbPool) -> None:
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER, lease_s=60)
    h = await health_row(pool, "c")
    assert h["circuit"] == "half_open"
    assert h["cooldown_until"] == LATER + timedelta(seconds=60)
    assert health.block_reason("active", h["circuit"], h["cooldown_until"], LATER) == "circuit_open"


async def test_row_half_open_second_claim_while_the_lease_runs_loses(pool: DbPool) -> None:
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER, lease_s=60)
    assert not await health.claim_probe(pool, "c", LATER + timedelta(seconds=59), lease_s=60)


async def test_row_half_open_lease_that_ran_out_can_be_taken_over(pool: DbPool) -> None:
    """A probe whose process died must not block the account for ever."""
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER, lease_s=60)
    after_lease = LATER + timedelta(seconds=61)
    h = await health_row(pool, "c")
    assert health.block_reason("active", h["circuit"], h["cooldown_until"], after_lease) is None
    assert await health.claim_probe(pool, "c", after_lease, lease_s=60)


async def test_row_half_open_success_closes_even_while_the_lease_runs(pool: DbPool) -> None:
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER)
    await health.record_success(pool, "c", now=LATER)
    h = await health_row(pool, "c")
    assert (h["circuit"], h["consecutive_failures"], h["cooldown_until"]) == ("closed", 0, None)


@pytest.mark.parametrize(
    "kind",
    [
        ErrorKind.SERVER,
        ErrorKind.TIMEOUT,
        ErrorKind.UNKNOWN,
        ErrorKind.RATE_LIMITED,
        ErrorKind.LIMIT_REACHED,
        ErrorKind.AUTH,
        ErrorKind.NEEDS_LOGIN,
        ErrorKind.EMPTY,
        ErrorKind.BAD_REQUEST,
    ],
)
async def test_row_half_open_any_failure_reopens_with_a_fresh_cooldown(pool: DbPool, kind: ErrorKind) -> None:
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER, lease_s=600)
    await health.record_failure(pool, "c", kind, "probe ended", now=LATER, retry_after_s=1)
    h = await health_row(pool, "c")
    assert h["circuit"] == "open", f"{kind.value}: the probe left the circuit {h['circuit']!r}"
    # fresh: the 120 s of an open circuit from now, not the 600 s lease (a provider cooldown may be longer)
    assert h["cooldown_until"] is not None and h["cooldown_until"] >= LATER + timedelta(seconds=120)
    if kind in (ErrorKind.SERVER, ErrorKind.TIMEOUT, ErrorKind.UNKNOWN, ErrorKind.RATE_LIMITED):
        assert h["cooldown_until"] == LATER + timedelta(seconds=120)


async def test_row_half_open_failure_with_backoff_doubles_the_open_time(pool: DbPool) -> None:
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER)
    await health.record_failure(pool, "c", ErrorKind.SERVER, "x", now=LATER, backoff=True)
    assert (await health_row(pool, "c"))["cooldown_until"] == LATER + timedelta(seconds=240)


async def test_row_half_open_release_makes_the_next_call_probe(pool: DbPool) -> None:
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER)
    assert await health.release_probe(pool, "c", LATER)
    assert (await health_row(pool, "c"))["circuit"] == "half_open"
    assert await health.claim_probe(pool, "c", LATER)


async def test_release_probe_after_the_probe_ended_changes_nothing(pool: DbPool) -> None:
    await open_circuit(pool)
    assert await health.claim_probe(pool, "c", LATER)
    await health.record_failure(pool, "c", ErrorKind.SERVER, "x", now=LATER)
    before = await health_row(pool, "c")
    assert not await health.release_probe(pool, "c", LATER)
    assert await health_row(pool, "c") == before


async def test_a_neutral_outcome_on_a_closed_circuit_still_leaves_no_trace(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    await health.record_failure(pool, "c", ErrorKind.EMPTY, "nothing", now=NOW)
    assert await fetch(pool, "select 1 from connection_health") == []


# --- the router ends every probe -----------------------------------------------------------------------------


async def _farm_with_open_reoon(
    pool: DbPool, farm_factory: Make, registry: Registry, executor: ScriptedExecutor
) -> tuple[FarmContext, FakeClock]:
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock, executors={"api": executor})
    for _ in range(5):
        await health.record_failure(pool, "reoon-01", ErrorKind.SERVER, "boom", now=NOW)
    clock.advance(125)
    return ctx, clock


async def test_a_cancelled_probe_is_given_back_and_the_next_call_probes(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor()
    executor.hold = True
    ctx, clock = await _farm_with_open_reoon(pool, farm_factory, registry, executor)

    call = asyncio.create_task(route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01"))
    await asyncio.wait_for(executor.started.wait(), 10)
    assert (await health_row(pool, "reoon-01"))["circuit"] == "half_open"
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    h = await health_row(pool, "reoon-01")
    assert h["circuit"] == "half_open" and h["cooldown_until"] == clock()  # free to probe again right now
    executor.hold = False
    out = await route(ctx, "verify_email", {"email": "b@example.com"}, caller="test", pin="reoon-01")
    assert out.ok
    assert (await health_row(pool, "reoon-01"))["circuit"] == "closed"


async def test_a_probe_that_ends_empty_reopens_the_circuit(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    empty = ExecResult(ok=True, data=ok_result().data, found=False, error_kind=ErrorKind.EMPTY, error="none")
    ctx, clock = await _farm_with_open_reoon(pool, farm_factory, registry, ScriptedExecutor(empty))

    await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01")

    h = await health_row(pool, "reoon-01")
    assert h["circuit"] == "open" and h["cooldown_until"] == clock() + timedelta(seconds=120)


async def test_a_probe_that_ends_rate_limited_reopens_the_circuit(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    limited = ExecResult(ok=False, error_kind=ErrorKind.RATE_LIMITED, error="429", retry_after_s=1)
    ctx, _ = await _farm_with_open_reoon(pool, farm_factory, registry, ScriptedExecutor(limited))

    await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01")

    assert (await health_row(pool, "reoon-01"))["circuit"] == "open"


async def test_a_probe_refused_before_the_call_is_given_back(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor()
    ctx, clock = await _farm_with_open_reoon(pool, farm_factory, registry, executor)
    async with pool.connection() as conn:  # no quota left: the reservation fails, the provider is never called
        await conn.execute("update public.consumption_units set limit_value = 0 where connection_id = 'reoon-01'")

    out = await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01")

    assert not out.ok and executor.calls == []
    h = await health_row(pool, "reoon-01")
    assert h["circuit"] == "half_open" and h["cooldown_until"] == clock()
    assert await health.claim_probe(pool, "reoon-01", clock())


# --- Q0-24 ---------------------------------------------------------------------------------------------------


async def test_record_failure_for_a_login_failure_needs_only_one_pooled_connection(db_url: str, pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    tiny: DbPool = AsyncConnectionPool(
        conninfo=db_url,
        min_size=1,
        max_size=1,
        kwargs={"autocommit": True, "prepare_threshold": None},
        open=False,
    )
    await tiny.open(wait=True, timeout=30)
    try:
        await asyncio.wait_for(
            health.record_failure(tiny, "c", ErrorKind.AUTH, "401 unauthorized", now=NOW), timeout=10
        )
    finally:
        await tiny.close()

    assert (await fetch(pool, "select status from connections where id = 'c'"))[0][0] == "needs_login"
    assert [a["ref"] for a in await list_open_alerts(pool)] == ["login:c"]


async def test_one_login_failure_raises_one_alert(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    executor = ScriptedExecutor(ExecResult(ok=False, error_kind=ErrorKind.AUTH, error="401"))
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01")
    assert [a["ref"] for a in await list_open_alerts(pool)] == ["login:reoon-01"]
