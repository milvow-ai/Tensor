"""Unit tests for ConnectionGate bulkhead gate (farm/resources/gate.py)."""

from __future__ import annotations

import asyncio
import time

import pytest

from farm.resources.gate import ConnectionGate, GateSaturated, clear_gates, get_gate


async def test_gate_basic_concurrency_semaphore() -> None:
    gate = ConnectionGate("test-conn", concurrency=2)
    assert gate.is_available()

    async with gate.acquire(max_wait_s=1.0):
        # One slot in use
        assert gate.is_available()
        async with gate.acquire(max_wait_s=1.0):
            # Both slots in use
            assert not gate.is_available()

    # Both released
    assert gate.is_available()


async def test_gate_concurrency_saturation_raises_gate_saturated() -> None:
    gate = ConnectionGate("test-conn", concurrency=1)

    async def hold_slot() -> None:
        async with gate.acquire(max_wait_s=1.0):
            await asyncio.sleep(0.2)

    task = asyncio.create_task(hold_slot())
    await asyncio.sleep(0.02)

    # Next attempt with small max_wait_s should fail with GateSaturated
    started = time.monotonic()
    with pytest.raises(GateSaturated) as exc_info:
        async with gate.acquire(max_wait_s=0.05):
            pass

    elapsed = time.monotonic() - started
    assert elapsed < 0.15
    assert "concurrency limit saturated" in str(exc_info.value)
    assert "test-conn" in str(exc_info.value)

    await task


async def test_gate_rate_limiter_saturation() -> None:
    # 2 requests per minute
    gate = ConnectionGate("rate-conn", concurrency=5, rate_per_min=2)

    async with gate.acquire(max_wait_s=1.0):
        pass
    async with gate.acquire(max_wait_s=1.0):
        pass

    # 3rd request within short window should saturate rate limit
    with pytest.raises(GateSaturated) as exc_info:
        async with gate.acquire(max_wait_s=0.05):
            pass

    assert "rate limit saturated" in str(exc_info.value)


async def test_get_gate_singleton_and_clear() -> None:
    clear_gates()
    g1 = get_gate("c1", concurrency=3, rate_per_min=60)
    g2 = get_gate("c1", concurrency=3, rate_per_min=60)
    assert g1 is g2

    # Different concurrency recreates
    g3 = get_gate("c1", concurrency=4, rate_per_min=60)
    assert g3 is not g1
    assert g3.concurrency == 4

    clear_gates()
    g4 = get_gate("c1", concurrency=4, rate_per_min=60)
    assert g4 is not g3
