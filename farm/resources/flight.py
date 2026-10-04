"""Result cache and cross-process single-flight ("flight"), both on one table: ``capability_requests``.

One row per ``request_hash`` (unique per workspace) is the whole protocol, so any number of Farm processes
agree without a coordinator:

=================  =========================================================================================
``running``        a leader is executing; ``expires_at`` is its lease, ``run_id`` its run
``succeeded``      ``result`` holds the answer; ``expires_at`` is the end of its cache life
``failed``         the last execution failed; failures are never cached (the next caller retries)
=================  =========================================================================================

``claim`` makes the caller the leader (insert, or a compare-and-swap takeover of a failed / expired /
abandoned row), serves it a cached answer, or makes it wait for the leader that is running now. A leader
that dies leaves a ``running`` row whose lease runs out; the next caller takes it over, so nothing waits for
a dead process longer than the lease. All time comparisons use the caller-supplied clock (never ``now()`` in
SQL) so tests can move time.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from farm.db.pool import DbPool
from farm.resources.trajectory import to_jsonable


class StoredResult(BaseModel):
    """What a succeeded request keeps: the normalised answer and where it came from."""

    data: dict[str, Any]
    provider: str
    connection_id: str
    found: bool | None = None


@dataclass(frozen=True)
class Leader:
    """The caller must execute; ``request_id`` is the row it holds."""

    request_id: UUID


@dataclass(frozen=True)
class Hit:
    """A cached answer that was already there."""

    stored: StoredResult
    expires_at: datetime
    run_id: UUID | None


@dataclass(frozen=True)
class Joined:
    """The outcome of an execution that another process was running while the caller waited."""

    stored: StoredResult | None
    error: dict[str, Any] | None
    run_id: UUID | None


type Claim = Leader | Hit | Joined


async def lookup_cache(pool: DbPool, request_hash: str, now: datetime) -> Hit | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select result, expires_at, run_id from public.capability_requests "
            "where request_hash = %s and status = 'succeeded' and expires_at > %s",
            (request_hash, now),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return Hit(stored=StoredResult.model_validate(row[0]), expires_at=row[1], run_id=row[2])


async def claim(
    pool: DbPool,
    clock: Callable[[], datetime],
    *,
    request_hash: str,
    capability: str,
    params: dict[str, Any],
    run_id: UUID,
    lease_s: float,
    poll_s: float,
) -> Claim:
    """Become the leader for ``request_hash``, or get the answer someone else produced (see module doc)."""
    observed_running: UUID | None = None
    while True:
        now = clock()
        lease_until = now + timedelta(seconds=lease_s)
        async with pool.connection() as conn:
            cur = await conn.execute(
                "insert into public.capability_requests "
                "(request_hash, capability, params, status, run_id, expires_at) "
                "values (%s, %s, %s, 'running', %s, %s) "
                "on conflict (workspace_id, request_hash) do nothing returning id",
                (request_hash, capability, Jsonb(to_jsonable(params, None)), run_id, lease_until),
            )
            inserted = await cur.fetchone()
            if inserted is not None:
                return Leader(inserted[0])
            cur = await conn.execute(
                "select id, status, result, run_id, expires_at from public.capability_requests "
                "where request_hash = %s",
                (request_hash,),
            )
            row = await cur.fetchone()
        if row is None:  # deleted between the insert and the select: try again
            continue
        row_id, status, result, row_run_id, expires_at = row
        live = expires_at is not None and expires_at > now

        if status == "succeeded" and live:
            stored = StoredResult.model_validate(result)
            if observed_running is not None:
                return Joined(stored=stored, error=None, run_id=row_run_id)
            return Hit(stored=stored, expires_at=expires_at, run_id=row_run_id)
        if status == "running" and live:
            observed_running = row_run_id
            await asyncio.sleep(poll_s)
            continue
        if status == "failed" and observed_running is not None and row_run_id == observed_running:
            return Joined(stored=None, error=(result or {}).get("error"), run_id=row_run_id)

        # failed earlier, expired, or a leader that died (lease over): take the row over, exactly one winner.
        async with pool.connection() as conn:
            cur = await conn.execute(
                "update public.capability_requests set status = 'running', run_id = %s, params = %s, "
                "result = null, expires_at = %s "
                "where id = %s and status = %s and run_id is not distinct from %s returning id",
                (run_id, Jsonb(to_jsonable(params, None)), lease_until, row_id, status, row_run_id),
            )
            won = await cur.fetchone()
        if won is not None:
            return Leader(row_id)
        observed_running = None  # lost the race: look at the row again from scratch


async def finish_success(
    pool: DbPool, request_id: UUID, run_id: UUID, stored: StoredResult, expires_at: datetime
) -> bool:
    """Publish the answer. False when the lease was lost to another process (nothing is overwritten)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "update public.capability_requests set status = 'succeeded', result = %s, expires_at = %s "
            "where id = %s and run_id = %s and status = 'running' returning id",
            (Jsonb(to_jsonable(stored, None)), expires_at, request_id, run_id),
        )
        return await cur.fetchone() is not None


async def finish_failure(
    pool: DbPool, request_id: UUID, run_id: UUID, error: dict[str, Any], now: datetime
) -> bool:
    """Record the failure (never cached: ``expires_at`` is already over) so waiters stop waiting."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "update public.capability_requests set status = 'failed', result = %s, expires_at = %s "
            "where id = %s and run_id = %s and status = 'running' returning id",
            (Jsonb(to_jsonable({"error": error}, None)), now, request_id, run_id),
        )
        return await cur.fetchone() is not None
