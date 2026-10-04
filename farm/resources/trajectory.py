"""Run trajectories: one ``runs`` row per call, one ``run_events`` row per decision the Farm makes.

``Trajectory`` is the writer used by the router (it owns the ``seq`` counter of its run, so events of one run
must be written by one flow at a time, which is how the router uses it). ``fetch_run`` is the reader behind
the ``get_run`` tool and the Console.

Events are observability; the quota ledger is money. A database error while writing an event is therefore
logged and swallowed (the call goes on, and the settle step still commits or releases its reservations)
instead of failing a call whose provider request is already on the wire. Programming errors still raise.
Every string in an event is passed through ``redact`` so a key can never reach the table.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from farm.db.pool import DbPool
from farm.secrets import redact

log = structlog.get_logger(__name__)

MAX_TEXT_CHARS = 1000


def to_jsonable(value: Any, max_chars: int | None = MAX_TEXT_CHARS) -> Any:
    """Plain-JSON copy of ``value``: UUID/date/Decimal converted, strings redacted, cut at ``max_chars``."""
    if isinstance(value, str):
        text = redact(value)
        return text if max_chars is None or len(text) <= max_chars else text[: max_chars - 1] + "…"
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump(mode="json"), max_chars)
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v, max_chars) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(v, max_chars) for v in value]
    return redact(str(value))


class Trajectory:
    """Writer for one run. Create with :meth:`start`."""

    def __init__(self, pool: DbPool, run_id: UUID, clock: Callable[[], datetime]) -> None:
        self._pool = pool
        self.run_id = run_id
        self._clock = clock
        self._seq = 0
        self.finished = False
        self.delegated = False
        """True while another task (the single-flight execution) owns this run and will finish it."""

    @classmethod
    async def start(
        cls,
        pool: DbPool,
        clock: Callable[[], datetime],
        *,
        capability: str,
        caller: str,
        strategy: str | None,
    ) -> Trajectory:
        run_id = uuid4()
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.runs (id, capability, caller, strategy, status, started_at) "
                "values (%s, %s, %s, %s, 'running', %s)",
                (run_id, capability, caller, strategy, clock()),
            )
        return cls(pool, run_id, clock)

    async def attach_request(self, request_id: UUID) -> None:
        """Link the run to its ``capability_requests`` row (known only after the claim)."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "update public.runs set request_id = %s where id = %s", (request_id, self.run_id)
            )

    async def event(self, kind: str, connection_id: str | None = None, **data: Any) -> None:
        self._seq += 1
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "insert into public.run_events (run_id, seq, kind, connection_id, data, at) "
                    "values (%s, %s, %s, %s, %s, %s)",
                    (self.run_id, self._seq, kind, connection_id, Jsonb(to_jsonable(data)), self._clock()),
                )
        except psycopg.Error as exc:
            log.error(
                "trajectory.event_lost",
                run_id=str(self.run_id),
                seq=self._seq,
                kind=kind,
                error=type(exc).__name__,
            )

    async def finish(
        self,
        *,
        status: str,
        cost_usd: Decimal,
        cached: bool,
        connection_id: str | None,
        error_kind: str | None = None,
        error: str | None = None,
    ) -> None:
        self.finished = True
        async with self._pool.connection() as conn:
            await conn.execute(
                "update public.runs set status = %s, cost_usd = %s, cached = %s, connection_id = %s, "
                "error_kind = %s, error = %s, finished_at = %s where id = %s",
                (
                    status,
                    cost_usd,
                    cached,
                    connection_id,
                    error_kind,
                    None if error is None else str(to_jsonable(error)),
                    self._clock(),
                    self.run_id,
                ),
            )


class RunEvent(BaseModel):
    seq: int
    kind: str
    connection_id: str | None
    data: dict[str, Any]
    at: datetime


class RunRecord(BaseModel):
    """A run and its trajectory, as ``get_run`` returns it."""

    run_id: UUID
    capability: str
    caller: str
    strategy: str | None
    status: str
    cost_usd: float
    cached: bool
    connection_id: str | None
    error_kind: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    events: list[RunEvent]


async def fetch_run(pool: DbPool, run_id: UUID) -> RunRecord | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select capability, caller, strategy, status, cost_usd, cached, connection_id, error_kind, "
            "error, started_at, finished_at from public.runs where id = %s",
            (run_id,),
        )
        run = await cur.fetchone()
        if run is None:
            return None
        cur = await conn.execute(
            "select seq, kind, connection_id, data, at from public.run_events where run_id = %s order by seq",
            (run_id,),
        )
        events = await cur.fetchall()
    return RunRecord(
        run_id=run_id,
        capability=run[0],
        caller=run[1],
        strategy=run[2],
        status=run[3],
        cost_usd=float(run[4]),
        cached=run[5],
        connection_id=run[6],
        error_kind=run[7],
        error=run[8],
        started_at=run[9],
        finished_at=run[10],
        events=[RunEvent(seq=e[0], kind=e[1], connection_id=e[2], data=e[3], at=e[4]) for e in events],
    )
