"""The Farm's runtime context: everything the router needs, passed explicitly (no module-level singletons).

``FarmContext`` carries the database pool, the executors by executor kind (``providers.executor``), an
injectable clock (tests move time to expire caches, cooldowns and leases), the timing policy and the
in-process single-flight table. The registry itself is not held here: the database is the runtime source of
truth (see ``farm.registry.sync``), and the router reads it on every call, so a change made in the Console
applies to the very next request.

``build_context()`` is the production wiring (``farm serve`` / ``farm call`` / ``farm status``); tests build
the dataclass directly with their own pool, executors and clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from farm.db.pool import DbPool, open_pool
from farm.executors.api import ApiExecutor
from farm.executors.base import Executor
from farm.executors.llm import LlmExecutor


def utc_now() -> datetime:
    return datetime.now(UTC)


@runtime_checkable
class _Closable(Protocol):
    async def aclose(self) -> None: ...


@dataclass
class FarmContext:
    pool: DbPool
    executors: dict[str, Executor]
    """Executor by kind (``api``, ``mcp``, ...). A provider whose kind has no executor is skipped with
    reason ``no_executor`` (visible in the run), never an error."""
    clock: Callable[[], datetime] = utc_now
    default_timeout_s: float = 30.0
    """Per-attempt timeout unless ``meta.timeout_s`` or the provider's ``config.timeout_s`` sets one."""
    deadline_s: float = 300.0
    """Upper bound for one request across all its attempts; the request lease outlives it (see router)."""
    poll_interval_s: float = 0.2
    """How often a caller polls a request that another process is executing."""
    owns_pool: bool = False
    flights: dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    """In-process single-flight: request hash -> the running execution (see ``farm.resources.router``)."""

    async def aclose(self) -> None:
        """Wait for running executions, close executors that need it, and the pool if this context owns it."""
        if self.flights:
            await asyncio.gather(*self.flights.values(), return_exceptions=True)
        for executor in self.executors.values():
            if isinstance(executor, _Closable):
                await executor.aclose()
        if self.owns_pool:
            await self.pool.close()


async def build_context(db_url: str | None = None) -> FarmContext:
    """Open the pool (``FARM_DB_URL`` / ``SUPABASE_DB_URL`` / embedded local Postgres) and wire the executors.

    Must run on a selector event loop (``farm.db.pool.run``). The router dispatches on ``providers.executor``,
    so supporting another kind (``mcp``, ``cli_agent``, ...) is one more entry in this mapping, added by the
    milestone that ships that executor.
    """
    pool = await open_pool(db_url)
    return FarmContext(pool=pool, executors={"api": ApiExecutor(), "llm": LlmExecutor()}, owns_pool=True)
