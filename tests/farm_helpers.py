"""Helpers shared by the router / gateway tests: readers for what the Farm wrote, and a scripted executor."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from farm.db.pool import DbPool
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.registry import Registry

EMAIL = "jane.doe@example.com"


async def fetch(pool: DbPool, query: str, *params: Any) -> list[tuple[Any, ...]]:
    async with pool.connection() as conn:
        cur = await conn.execute(query, params)  # type: ignore[call-overload]
        return [tuple(r) for r in await cur.fetchall()]


async def events(pool: DbPool, run_id: UUID | str) -> list[tuple[str, str | None, dict[str, Any]]]:
    """``(kind, connection_id, data)`` of a run, in seq order."""
    rows = await fetch(
        pool, "select kind, connection_id, data from run_events where run_id = %s order by seq", run_id
    )
    return [(k, c, d) for k, c, d in rows]


def kinds(evts: list[tuple[str, str | None, dict[str, Any]]]) -> list[tuple[str, str | None]]:
    return [(k, c) for k, c, _ in evts]


async def reservations(pool: DbPool, connection_id: str | None = None) -> list[tuple[str, str, str, Any]]:
    """``(connection_id, unit, status, actual)`` of every reservation, oldest first."""
    rows = await fetch(
        pool,
        "select connection_id, unit, status, actual from quota_reservations "
        "where (%s::text is null or connection_id = %s) order by created_at, id",
        connection_id,
        connection_id,
    )
    return rows  # type: ignore[return-value]


async def quota(pool: DbPool, connection_id: str, unit: str = "credits") -> tuple[Any, Any]:
    """``(used, reserved)`` summed over periods."""
    rows = await fetch(
        pool,
        "select coalesce(sum(used), 0), coalesce(sum(reserved), 0) from quota_usage "
        "where connection_id = %s and unit = %s",
        connection_id,
        unit,
    )
    return rows[0]


async def health(pool: DbPool, connection_id: str) -> dict[str, Any]:
    rows = await fetch(
        pool,
        "select circuit, consecutive_failures, last_error_kind, cooldown_until, success_count, failure_count "
        "from connection_health where connection_id = %s",
        connection_id,
    )
    assert rows, f"no connection_health row for {connection_id}"
    keys = (
        "circuit",
        "consecutive_failures",
        "last_error_kind",
        "cooldown_until",
        "success_count",
        "failure_count",
    )
    return dict(zip(keys, rows[0], strict=True))


async def status_of(pool: DbPool, connection_id: str) -> str:
    return str((await fetch(pool, "select status from connections where id = %s", connection_id))[0][0])


async def run_row(pool: DbPool, run_id: UUID | str) -> dict[str, Any]:
    rows = await fetch(
        pool,
        "select status, cost_usd, cached, connection_id, error_kind, finished_at is not null from runs where id = %s",
        run_id,
    )
    keys = ("status", "cost_usd", "cached", "connection_id", "error_kind", "finished")
    return dict(zip(keys, rows[0], strict=True))


def normalise(registry: Registry) -> dict[str, Any]:
    """Order-insensitive comparable form of a registry: connections by id (route order is meaningful, kept)."""
    data: dict[str, Any] = registry.model_dump(mode="json")
    for provider in data["providers"].values():
        provider["connections"].sort(key=lambda c: c["id"])
    return data


def only_reoon_01(registry: Registry) -> Registry:
    """The fixture registry with a single Reoon account (so 'one Reoon call' means one)."""
    registry.providers["reoon"].connections = registry.providers["reoon"].connections[:1]
    return registry


class ScriptedExecutor:
    """An executor that answers from a script, records every call and can be slowed down or made to crash.

    ``script`` entries are consumed in order (the last one repeats): an ``ExecResult``, an exception (raised),
    or a callable ``(ExecRequest) -> ExecResult``. ``delay_s`` simulates a slow provider.
    """

    def __init__(
        self, *script: ExecResult | BaseException | Callable[[ExecRequest], ExecResult], delay_s: float = 0.0
    ):
        self.script = list(script) or [ok_result()]
        self.calls: list[ExecRequest] = []
        self.delay_s = delay_s
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.hold = False

    async def execute(self, req: ExecRequest) -> ExecResult:
        self.calls.append(req)
        self.started.set()
        if self.hold:
            await self.release.wait()
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(req)
        return step


class AsyncFnExecutor:
    """An executor backed by an async function (for tests that need to do something around the call)."""

    def __init__(self, fn: Callable[[ExecRequest], Awaitable[ExecResult]]) -> None:
        self._fn = fn

    async def execute(self, req: ExecRequest) -> ExecResult:
        return await self._fn(req)


def ok_result(
    status: str = "valid", *, units: dict[str, float] | None = None, found: bool | None = True
) -> ExecResult:
    return ExecResult(
        ok=True,
        data={
            "email": EMAIL,
            "status": status,
            "sub_status": None,
            "provider": "scripted",
            "checked_at": "2026-10-04T12:00:00Z",
        },
        found=found,
        units_used={"credits": 1.0} if units is None else units,
    )


def failure(kind: ErrorKind, message: str = "boom", **extra: Any) -> ExecResult:
    return ExecResult(ok=False, error_kind=kind, error=message, **extra)
