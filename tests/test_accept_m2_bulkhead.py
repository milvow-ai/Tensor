"""M2 acceptance: bulkhead isolation across connections (farm/resources/gate.py).

Connection A (concurrency 1) is held busy by a slow mocked call; concurrent requests
bypass A within max_queue_wait_s and are served by connection B without stalling.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ExecRequest, ExecResult
from farm.registry import Registry
from farm.resources.gate import clear_gates
from farm.resources.router import route
from tests.farm_helpers import AsyncFnExecutor, ScriptedExecutor, events, kinds, ok_result

type Make = Callable[..., Awaitable[FarmContext]]


async def test_bulkhead_slow_connection_a_bypassed_to_b_within_max_queue_wait(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    clear_gates()
    # Configure reoon-01 with concurrency 1 and small max_queue_wait_s (0.05s)
    c1 = registry.providers["reoon"].connections[0]
    c1.concurrency = 1
    c1.priority = 1
    c1.meta["max_queue_wait_s"] = 0.05

    # Configure reoon-02 with concurrency 5 (priority 2)
    c2 = registry.providers["reoon"].connections[1]
    c2.concurrency = 5
    c2.priority = 2

    # reoon-01 is held slow (0.4s), reoon-02 is fast (immediate)
    async def scripted_call(req: ExecRequest) -> ExecResult:
        if req.connection.id == "reoon-01":
            await asyncio.sleep(0.4)
            return ok_result()
        return ok_result()

    executor = AsyncFnExecutor(scripted_call)
    ctx = await farm_factory(registry, executors={"api": executor})

    # Start slow call on reoon-01 in the background
    slow_task = asyncio.create_task(route(ctx, "verify_email", {"email": "slow@example.com"}, caller="test"))
    # Wait briefly for slow call to acquire reoon-01 gate
    await asyncio.sleep(0.02)

    # 5 concurrent requests while reoon-01 is held busy
    t0 = time.monotonic()
    fast_results = await asyncio.gather(
        *(
            route(ctx, "verify_email", {"email": f"user{i}@example.com"}, caller="test")
            for i in range(5)
        )
    )
    t_elapsed = time.monotonic() - t0

    # All 5 requests completed well before the slow call finished
    assert t_elapsed < 0.25, f"fast calls took {t_elapsed:.3f}s, expected < 0.25s"

    # All 5 requests were successfully served by reoon-02 (bypassing saturated reoon-01)
    for res in fast_results:
        assert res.ok
        assert res.source is not None and res.source.connection_id == "reoon-02"
        # Verify skip event recorded saturated reason
        run_kinds = kinds(await events(pool, res.run_id))
        assert ("skip", "reoon-01") in run_kinds
        assert ("candidate", "reoon-02") in run_kinds

    # The slow call on reoon-01 completes cleanly
    slow_res = await slow_task
    assert slow_res.ok
    assert slow_res.source is not None and slow_res.source.connection_id == "reoon-01"


async def test_bulkhead_rate_limit_saturation_bypasses_to_next_candidate(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    clear_gates()
    # Configure reoon-01 with rate limit of 2 requests per minute and small wait
    c1 = registry.providers["reoon"].connections[0]
    c1.concurrency = 5
    c1.rate_per_min = 2
    c1.priority = 1
    c1.meta["max_queue_wait_s"] = 0.05

    # Configure reoon-02 without rate limit (priority 2)
    c2 = registry.providers["reoon"].connections[1]
    c2.concurrency = 5
    c2.priority = 2

    executor = ScriptedExecutor(lambda req: ok_result())
    ctx = await farm_factory(registry, executors={"api": executor})

    # First 2 calls use reoon-01 within rate limit
    r1 = await route(ctx, "verify_email", {"email": "r1@example.com"}, caller="test")
    r2 = await route(ctx, "verify_email", {"email": "r2@example.com"}, caller="test")
    assert r1.ok and r1.source is not None and r1.source.connection_id == "reoon-01"
    assert r2.ok and r2.source is not None and r2.source.connection_id == "reoon-01"

    # 3rd call saturates reoon-01 rate limit -> immediately bypasses to reoon-02
    r3 = await route(ctx, "verify_email", {"email": "r3@example.com"}, caller="test")
    assert r3.ok and r3.source is not None and r3.source.connection_id == "reoon-02"
    trail = kinds(await events(pool, r3.run_id))
    assert ("skip", "reoon-01") in trail
