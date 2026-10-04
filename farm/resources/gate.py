"""Bulkhead gate per connection (HANDOFF 4.8): concurrency and rate limit management.

A saturated connection makes the router try the next candidate if waiting would exceed
`meta.max_queue_wait_s` (default 2 s), instead of blocking the whole request.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiolimiter


class GateSaturated(Exception):
    """Raised when acquiring the bulkhead gate exceeds the maximum queue wait time."""


class ConnectionGate:
    def __init__(self, connection_id: str, concurrency: int = 1, rate_per_min: int | None = None) -> None:
        self.connection_id = connection_id
        self.concurrency = max(1, concurrency)
        self.semaphore = asyncio.Semaphore(self.concurrency)
        self.rate_per_min = rate_per_min
        self.limiter = (
            aiolimiter.AsyncLimiter(float(rate_per_min), 60.0)
            if rate_per_min is not None and rate_per_min > 0
            else None
        )

    def is_available(self) -> bool:
        """Fast non-blocking check whether the gate currently has immediate capacity."""
        if self.semaphore.locked():
            return False
        if self.limiter is not None and not self.limiter.has_capacity():
            return False
        return True

    @asynccontextmanager
    async def acquire(self, max_wait_s: float = 2.0) -> AsyncIterator[None]:
        """Acquire bulkhead concurrency and rate limits within max_wait_s or raise GateSaturated."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(max_wait_s):
                await self.semaphore.acquire()
        except TimeoutError as exc:
            raise GateSaturated(
                f"connection '{self.connection_id}' concurrency limit saturated (waited > {max_wait_s:g}s)"
            ) from exc

        try:
            if self.limiter is not None:
                elapsed = time.monotonic() - start
                remaining = max(0.0, max_wait_s - elapsed)
                try:
                    async with asyncio.timeout(remaining):
                        await self.limiter.acquire()
                except TimeoutError as exc:
                    raise GateSaturated(
                        f"connection '{self.connection_id}' rate limit saturated (waited > {max_wait_s:g}s)"
                    ) from exc
            yield
        finally:
            self.semaphore.release()


_GATES: dict[str, ConnectionGate] = {}


def get_gate(
    connection_id: str,
    concurrency: int = 1,
    rate_per_min: int | None = None,
) -> ConnectionGate:
    """Get or create the singleton ConnectionGate for a connection."""
    gate = _GATES.get(connection_id)
    if gate is None or gate.concurrency != max(1, concurrency) or gate.rate_per_min != rate_per_min:
        gate = ConnectionGate(connection_id, concurrency, rate_per_min)
        _GATES[connection_id] = gate
    return gate


def clear_gates() -> None:
    """Clear all cached gates (used in test teardown)."""
    _GATES.clear()
