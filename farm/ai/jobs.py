"""AI jobs: non-blocking work on other AIs, run inside the Farm process.

``JobStore`` is the ``ai_jobs`` table: every state change is one guarded UPDATE (``where state = ...``), so
two actors (the runner, a cancel, a sweep from another process) can never both win. ``JobManager`` is the
in-process scheduler and runner on top of it.

Life of a job::

    ai_start -> account chosen, row ``queued`` -> waits for a free slot of its account (and pool)
             -> ``running``: ``route(..., "ask_ai", pin=account)``. Quota reserve/commit, cost, health, alerts
                and the ``runs`` trajectory are the router's, as for the blocking ask_ai; the CLI runs on the
                thread runner
             -> ``succeeded`` (answer in a result file) | ``failed`` (kind, cause, retry_at) | ``cancelled``
             -> or, with ``retry_other_account`` and a first turn that failed on its account (limit / auth /
                crash / account unavailable): back to ``queued`` on the next usable account, every attempt
                recorded

Guarantees (each one tested in ``tests/test_ai_jobs.py`` / ``tests/test_accept_aip2.py``):

* A job runs in a task of the Farm process, not of the MCP request, so it goes on when the caller disconnects.
* At most ``AccountInfo.max_parallel`` jobs per account and ``providers.config.max_parallel`` per pool run at
  once; the rest queue in submission order. A call that finds the account's gate taken by another call
  (``Busy``) goes back to the queue instead of failing.
* ``ai_cancel`` kills the CLI's process tree (the runner's cancellation path) and settles the quota
  reservation through the router before it returns. A cancel from another process (``farm ai cancel``) is a
  ``cancel_requested_at`` the owner picks up on its next tick.
* A job is owned by one Farm process, which renews ``heartbeat_at``. When the owner stops (crash, kill) its
  jobs are failed as ``farm_restart`` by any other process once the heartbeat is older than the lease: never
  silently re-run, and never failed while their owner is alive. A graceful stop (``shutdown``) does it
  at once.
* One queued/running job per conversation (unique index), so two turns never race on one native session.
* The task text lives in memory only; the answer is a file (``farm.ai.results``). The database gets the
  trajectory: state, account, model, tokens, cost, native session id, attempts, error.

Limits worth knowing: the CLI runner uses the event loop's default thread pool, which bounds how many CLI
processes run at once to ``min(32, cpu_count + 4)``; ``connections.concurrency`` is enforced per Farm process.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, LiteralString, Protocol
from uuid import UUID, uuid4

import psycopg
import structlog
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from farm.ai import results
from farm.ai.accounts import (
    ASK_AI,
    AccountInfo,
    Wish,
    load_accounts,
    plan_accounts,
    rank,
    refuse_single,
    usable,
)
from farm.ai.conversations import (
    ConversationRecord,
    ConversationStore,
    apply_turn,
    insert_conversation,
    move_to_account,
    point_to_job,
)
from farm.ai.failures import AiRequestError, Busy, Failure, classify, retryable_on_another_account
from farm.capabilities.schemas import (
    TERMINAL_STATES,
    AiAttempt,
    AiFailure,
    AiJobResult,
    AiJobSpec,
    AiJobStatus,
    AiStarted,
)
from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.resources.router import UnknownCapability, route
from farm.secrets import redact

log = structlog.get_logger(__name__)

HEARTBEAT_S = 10.0
LEASE_S = 60.0
TICK_S = 1.0
WAIT_POLL_S = 1.0
BUSY_BACKOFF_S = 2.0
DEADLINE_MARGIN_S = 60.0
CANCEL_WAIT_S = 15.0
STATUS_WINDOW = timedelta(hours=24)
STATUS_LIMIT = 50
MAX_ERROR_CHARS = 500

FARM_RESTART_MESSAGE = "the Farm process running this job stopped; the job was not re-run"


# --- the rows -----------------------------------------------------------------------------------------


class JobRecord(BaseModel):
    """A row of ``ai_jobs``. The field names are the column names (``JobStore`` selects them by name)."""

    id: UUID
    conversation_id: UUID
    turn: int
    ai: str
    account: str
    model: str | None
    mode: str
    cwd: str | None
    state: str
    timeout_s: int
    retry_other_account: bool
    caller: str
    task_chars: int
    json_schema: dict[str, Any] | None
    resume_session_id: str | None
    native_session_id: str | None
    owner_id: UUID | None
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    attempts: list[dict[str, Any]]
    run_id: UUID | None
    result_path: str | None
    result_chars: int | None
    json_valid: bool | None
    json_errors: list[str] | None
    files_changed: list[str] | None
    usage: dict[str, float]
    tokens: int | None
    cost_usd: Decimal
    cost_estimated: bool
    duration_s: Decimal | None
    error_kind: str | None
    error_message: str | None
    error_cause: str | None
    retry_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES


_JOB_COLUMNS: LiteralString = (
    "id, conversation_id, turn, ai, account, model, mode, cwd, state, timeout_s, "
    "retry_other_account, caller, "
    "task_chars, json_schema, resume_session_id, native_session_id, owner_id, heartbeat_at, "
    "cancel_requested_at, attempts, run_id, result_path, result_chars, json_valid, json_errors, "
    "files_changed, usage, tokens, cost_usd, cost_estimated, duration_s, error_kind, error_message, "
    "error_cause, retry_at, created_at, started_at, finished_at"
)
_JOB_SELECT: LiteralString = "select " + _JOB_COLUMNS + " from public.ai_jobs "
_JOB_RETURNING: LiteralString = " returning " + _JOB_COLUMNS


@dataclass(frozen=True)
class Finish:
    """Everything a job's final UPDATE writes (success, failure and cancel share it)."""

    state: Literal["succeeded", "failed", "cancelled"]
    account: str
    attempts: list[dict[str, Any]]
    run_id: UUID | None = None
    cost_usd: Decimal = Decimal(0)
    cost_estimated: bool = True
    usage: dict[str, float] = field(default_factory=dict)
    tokens: int | None = None
    duration_s: float | None = None
    native_session_id: str | None = None
    stored: results.StoredResult | None = None
    json_valid: bool | None = None
    json_errors: list[str] | None = None
    files_changed: list[str] | None = None
    failure: Failure | None = None
    retry_at: datetime | None = None


class JobStore:
    """The ``ai_jobs`` table. Every method is one statement or one transaction; none keeps state."""

    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        conversation: ConversationRecord | None,
        ai: str,
        account: str,
        spec: AiJobSpec,
        caller: str,
        owner_id: UUID,
        now: datetime,
    ) -> JobRecord:
        """Insert a ``queued`` job (and its conversation when it is the first turn) in one transaction.

        Raises ``AiRequestError('conversation_busy')`` when the conversation has a queued or running job.
        """
        job_id = uuid4()
        try:
            async with self._pool.connection() as conn, conn.transaction():
                resume: str | None = None
                if conversation is None:
                    conversation_id = await insert_conversation(conn, ai=ai, account=account)
                    turn = 1
                else:
                    cur = await conn.execute(
                        "select turns, native_session_id from public.ai_conversations "
                        "where id = %s for update",
                        (conversation.id,),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        raise AiRequestError("not_found", f"conversation {conversation.id} does not exist")
                    conversation_id, turn, resume = conversation.id, int(row[0]) + 1, row[1]
                async with conn.cursor(row_factory=class_row(JobRecord)) as cur2:
                    await cur2.execute(
                        """
                        insert into public.ai_jobs (
                          id, conversation_id, turn, ai, account, model, mode, cwd, timeout_s,
                          retry_other_account, caller, task_chars, json_schema, resume_session_id,
                          owner_id, heartbeat_at, created_at)
                        values (
                          %(id)s, %(conversation)s, %(turn)s, %(ai)s, %(account)s, %(model)s, %(mode)s,
                          %(cwd)s, %(timeout)s, %(retry)s, %(caller)s, %(chars)s, %(schema)s, %(resume)s,
                          %(owner)s, %(now)s, %(now)s)
                        """
                        + _JOB_RETURNING,
                        {
                            "id": job_id,
                            "conversation": conversation_id,
                            "turn": turn,
                            "ai": ai,
                            "account": account,
                            "model": spec.model,
                            "mode": spec.mode,
                            "cwd": spec.cwd,
                            "timeout": spec.timeout_s,
                            "retry": spec.retry_other_account,
                            "caller": caller,
                            "chars": len(spec.task),
                            "schema": None if spec.json_schema is None else Jsonb(spec.json_schema),
                            "resume": resume,
                            "owner": owner_id,
                            "now": now,
                        },
                    )
                    record = await cur2.fetchone()
                if record is None:
                    raise RuntimeError("insert into ai_jobs returned no row")
                await point_to_job(conn, conversation_id, job_id)
        except psycopg.errors.UniqueViolation:
            where = "" if conversation is None else f" {conversation.id}"
            raise AiRequestError(
                "conversation_busy",
                f"conversation{where} still has a queued or running job; wait for it (ai_wait) or cancel it",
                account=account,
            ) from None
        return record

    async def get(self, job_id: UUID) -> JobRecord | None:
        found = await self.get_many([job_id])
        return found[0] if found else None

    async def get_many(self, job_ids: Sequence[UUID]) -> list[JobRecord]:
        """The jobs that exist, in the order asked for."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=class_row(JobRecord)) as cur:
            await cur.execute(_JOB_SELECT + "where id = any(%s)", (list(job_ids),))
            by_id = {r.id: r for r in await cur.fetchall()}
        return [by_id[i] for i in job_ids if i in by_id]

    async def recent(self, *, since: datetime, limit: int) -> list[JobRecord]:
        """Queued and running jobs plus the ones that finished since ``since``, newest first."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=class_row(JobRecord)) as cur:
            await cur.execute(
                _JOB_SELECT + "where state in ('queued', 'running') or finished_at >= %s "
                "order by created_at desc, id limit %s",
                (since, limit),
            )
            return await cur.fetchall()

    async def newest(self, *, state: str | None, limit: int) -> list[JobRecord]:
        """The newest jobs, optionally only those in ``state`` (``farm ai jobs``)."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=class_row(JobRecord)) as cur:
            await cur.execute(
                _JOB_SELECT + "where (%(state)s::text is null or state = %(state)s) "
                "order by created_at desc, id limit %(limit)s",
                {"state": state, "limit": limit},
            )
            return await cur.fetchall()

    async def mark_running(self, job_id: UUID, owner_id: UUID, now: datetime) -> JobRecord | None:
        """``queued`` -> ``running`` for the owner; ``None`` when the job is no longer queued."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=class_row(JobRecord)) as cur:
            await cur.execute(
                """
                update public.ai_jobs set state = 'running', started_at = coalesce(started_at, %(now)s),
                  owner_id = %(owner)s, heartbeat_at = %(now)s
                where id = %(id)s and state = 'queued'
                """
                + _JOB_RETURNING,
                {"id": job_id, "owner": owner_id, "now": now},
            )
            return await cur.fetchone()

    async def requeue(
        self, job: JobRecord, owner_id: UUID, *, account: str, attempts: list[dict[str, Any]], now: datetime
    ) -> bool:
        """``running`` -> ``queued`` on ``account`` (a busy account, or the next account of a retry)."""
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "update public.ai_jobs set state = 'queued', account = %s, attempts = %s, heartbeat_at = %s "
                "where id = %s and owner_id = %s and state = 'running' returning id",
                (account, Jsonb(attempts), now, job.id, owner_id),
            )
            if await cur.fetchone() is None:
                return False
            await move_to_account(conn, job.conversation_id, account)
        return True

    async def finish(self, job: JobRecord, owner_id: UUID | None, fin: Finish, now: datetime) -> bool:
        """The final UPDATE of a job and its conversation, atomically; ``False``: it had already ended."""
        failure = fin.failure
        stored = fin.stored
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """
                update public.ai_jobs set
                  state = %(state)s, account = %(account)s, attempts = %(attempts)s, run_id = %(run_id)s,
                  native_session_id = %(session)s, result_path = %(path)s, result_chars = %(chars)s,
                  result_sha256 = %(sha)s, json_valid = %(json_valid)s, json_errors = %(json_errors)s,
                  files_changed = %(files)s, usage = %(usage)s, tokens = %(tokens)s, cost_usd = %(cost)s,
                  cost_estimated = %(estimated)s, duration_s = %(duration)s, error_kind = %(kind)s,
                  error_message = %(message)s, error_cause = %(cause)s, retry_at = %(retry_at)s,
                  finished_at = %(now)s, heartbeat_at = %(now)s
                where id = %(id)s and state in ('queued', 'running')
                  and (%(owner)s::uuid is null or owner_id = %(owner)s)
                returning id
                """,
                {
                    "id": job.id,
                    "owner": owner_id,
                    "state": fin.state,
                    "account": fin.account,
                    "attempts": Jsonb(fin.attempts),
                    "run_id": fin.run_id,
                    "session": fin.native_session_id,
                    "path": None if stored is None else str(stored.path),
                    "chars": None if stored is None else stored.chars,
                    "sha": None if stored is None else stored.sha256,
                    "json_valid": fin.json_valid,
                    "json_errors": None if fin.json_errors is None else Jsonb(fin.json_errors),
                    "files": None if fin.files_changed is None else Jsonb(fin.files_changed),
                    "usage": Jsonb(fin.usage),
                    "tokens": fin.tokens,
                    "cost": fin.cost_usd,
                    "estimated": fin.cost_estimated,
                    "duration": fin.duration_s,
                    "kind": None if failure is None else failure.kind,
                    "message": None if failure is None else redact(failure.message)[:MAX_ERROR_CHARS],
                    "cause": None if failure is None else failure.cause,
                    "retry_at": fin.retry_at,
                    "now": now,
                },
            )
            if await cur.fetchone() is None:
                return False
            await apply_turn(
                conn,
                job.conversation_id,
                job_id=job.id,
                account=fin.account,
                native_session_id=fin.native_session_id,
                tokens=fin.tokens or 0,
                cost_usd=fin.cost_usd,
                succeeded=fin.state == "succeeded",
            )
        return True

    async def request_cancel(self, job_id: UUID, now: datetime) -> JobRecord | None:
        """Ask the owner of a queued or running job to stop it (the owner acts on its next tick)."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=class_row(JobRecord)) as cur:
            await cur.execute(
                "update public.ai_jobs set cancel_requested_at = coalesce(cancel_requested_at, %s) "
                "where id = %s and state in ('queued', 'running')" + _JOB_RETURNING,
                (now, job_id),
            )
            return await cur.fetchone()

    async def cancel_requests(self, owner_id: UUID) -> list[UUID]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select id from public.ai_jobs where owner_id = %s and cancel_requested_at is not null "
                "and state in ('queued', 'running')",
                (owner_id,),
            )
            return [UUID(str(r[0])) for r in await cur.fetchall()]

    async def heartbeat(self, owner_id: UUID, now: datetime) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "update public.ai_jobs set heartbeat_at = %s "
                "where owner_id = %s and state in ('queued', 'running')",
                (now, owner_id),
            )
            return cur.rowcount

    async def sweep_orphans(self, owner_id: UUID, now: datetime, lease_s: float) -> list[JobRecord]:
        """Fail the jobs of other Farm processes whose heartbeat is older than the lease: ``farm_restart``."""
        async with self._pool.connection() as conn, conn.cursor(row_factory=class_row(JobRecord)) as cur:
            await cur.execute(
                """
                update public.ai_jobs set state = 'failed', error_kind = 'farm_restart',
                  error_message = %(message)s, finished_at = %(now)s
                where state in ('queued', 'running') and owner_id is distinct from %(me)s
                  and (heartbeat_at is null or heartbeat_at < %(cutoff)s)
                """
                + _JOB_RETURNING,
                {
                    "message": FARM_RESTART_MESSAGE,
                    "now": now,
                    "me": owner_id,
                    "cutoff": now - timedelta(seconds=lease_s),
                },
            )
            return await cur.fetchall()


# --- views ----------------------------------------------------------------------------------------------


def _elapsed_s(record: JobRecord, now: datetime) -> float:
    start = record.created_at if record.state == "queued" else (record.started_at or record.created_at)
    end = record.finished_at if record.finished and record.finished_at else now
    return max(0.0, (end - start).total_seconds())


def failure_of(record: JobRecord) -> AiFailure | None:
    if record.error_kind is None:
        return None
    return AiFailure.model_validate(
        {
            "kind": record.error_kind,
            "ai": record.ai,
            "account": record.account,
            "message": record.error_message or "",
            "retry_at": record.retry_at,
            "cause": record.error_cause,
        }
    )


def status_of(record: JobRecord, now: datetime, jobs_ahead: int | None = None) -> AiJobStatus:
    return AiJobStatus.model_validate(
        {
            "job_id": record.id,
            "conversation_id": record.conversation_id,
            "turn": record.turn,
            "state": record.state,
            "ai": record.ai,
            "account": record.account,
            "model": record.model,
            "mode": record.mode,
            "jobs_ahead": jobs_ahead if record.state == "queued" else None,
            "elapsed_s": _elapsed_s(record, now),
            "tokens": record.tokens,
            "attempt_count": len(record.attempts),
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "error": failure_of(record),
        }
    )


async def result_of(record: JobRecord, now: datetime, *, include_text: bool = True) -> AiJobResult:
    """The status of a finished job plus its exact answer (inline up to the limit, else path + preview)."""
    text: str | None = None
    truncated = False
    parsed: Any = None
    note: str | None = None
    if record.state == "succeeded" and record.result_path:
        stored = await asyncio.to_thread(results.read_result, record.result_path)
        if stored is None:
            note = f"the result file {record.result_path} is gone: the answer can no longer be read"
        else:
            if record.json_schema is not None:
                parsed = results.check_json(stored, None, record.json_schema).parsed
            if include_text:
                if len(stored) > results.inline_limit():
                    text = results.preview(stored)
                    truncated = len(text) < len(stored)
                else:
                    text = stored
    return AiJobResult.model_validate(
        {
            **status_of(record, now).model_dump(),
            "ok": record.state == "succeeded",
            "text": text,
            "text_truncated": truncated,
            "note": note,
            "result_path": record.result_path,
            "result_chars": record.result_chars,
            "json_data": parsed,
            "json_valid": record.json_valid,
            "json_errors": record.json_errors or [],
            "native_session_id": record.native_session_id,
            "usage": record.usage,
            "cost_usd": float(record.cost_usd),
            "cost_estimated": record.cost_estimated,
            "duration_s": None if record.duration_s is None else float(record.duration_s),
            "files_changed": record.files_changed,
            "run_id": record.run_id,
            "attempts": record.attempts,
        }
    )


# --- the manager ------------------------------------------------------------------------------------------


@dataclass(eq=False)
class _Work:
    """A job this process owns: the task text (memory only), where it runs and what it holds."""

    record: JobRecord
    task: str
    seq: int
    ai: str
    account: str
    limit: int
    pool_limit: int | None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)
    cost: Decimal = Decimal(0)
    attempt_started_at: datetime | None = None
    not_before: float = 0.0
    busy_since: float | None = None
    cancel_reason: Literal["user", "shutdown"] | None = None
    handle: asyncio.Task[None] | None = None
    slot: tuple[str, str] | None = None

    @property
    def job_id(self) -> UUID:
        return self.record.id


@dataclass(frozen=True)
class Attempt:
    """What one try of one turn came to: an answer (``data``), a failure, or ``busy`` (nothing ran)."""

    account: str
    started_at: datetime
    finished_at: datetime
    run_id: UUID | None = None
    cost_usd: Decimal = Decimal(0)
    data: dict[str, Any] | None = None
    failure: Failure | None = None
    busy: bool = False
    retry_at: datetime | None = None


class Transport(Protocol):
    """How one turn of a job reaches its worker.

    ``RouterTransport`` runs a one-shot CLI through the router. A transport that keeps a persistent streaming
    session with the worker (ACP) implements the same method and plugs in behind the same job and conversation
    API: queueing, limits, retry, cancel, restart recovery and the stored trajectory do not change.
    """

    async def run(self, job: JobRecord, task: str) -> Attempt:
        """Run the turn on ``job.account`` (resuming ``job.resume_session_id``); cancel = kill the worker."""
        ...


class RouterTransport:
    """One-shot CLI workers (the M3e executors) through ``route``, pinned to the job's account.

    Quota reserve/commit, cost, health, alerts and the ``runs`` trajectory are the router's, as for the
    blocking ``ask_ai``.
    """

    def __init__(self, ctx: FarmContext) -> None:
        self._ctx = ctx

    async def run(self, job: JobRecord, task: str) -> Attempt:
        params: dict[str, Any] = {"task": task, "mode": job.mode, "timeout_s": job.timeout_s}
        for key, value in (
            ("model", job.model),
            ("cwd", job.cwd),
            ("json_schema", job.json_schema),
            ("session_id", job.resume_session_id),
        ):
            if value is not None:
                params[key] = value
        # The router cuts a call off at ctx.default_timeout_s (30 s unless the connection says otherwise)
        # and a request at ctx.deadline_s: both far below an hours-long job, so a job gets limits of its own.
        job_ctx = replace(
            self._ctx,
            default_timeout_s=float(job.timeout_s),
            deadline_s=float(job.timeout_s) + DEADLINE_MARGIN_S,
            owns_pool=False,
        )
        started = self._ctx.clock()
        try:
            outcome = await route(job_ctx, ASK_AI, params, pin=job.account, caller=job.caller)
        except UnknownCapability as exc:
            return Attempt(job.account, started, self._ctx.clock(), failure=Failure("bad_request", str(exc)))
        finished = self._ctx.clock()
        cost = Decimal(str(outcome.cost.usd))
        if outcome.ok and outcome.result is not None:
            return Attempt(
                job.account, started, finished, run_id=outcome.run_id, cost_usd=cost, data=outcome.result
            )
        failure: Failure | Busy
        if outcome.error is None:
            failure = Failure("crash", "the router returned neither an answer nor an error")
        else:
            failure = classify(outcome.error, account=job.account, replying=job.resume_session_id is not None)
        if isinstance(failure, Busy):
            return Attempt(job.account, started, finished, run_id=outcome.run_id, cost_usd=cost, busy=True)
        retry_at = None
        if failure.kind in ("limit", "account_unavailable"):
            found = await load_accounts(self._ctx.pool, ids=[job.account])
            retry_at = found[0].retry_at(self._ctx.clock()) if found else None
        return Attempt(
            job.account,
            started,
            finished,
            run_id=outcome.run_id,
            cost_usd=cost,
            failure=failure,
            retry_at=retry_at,
        )


@dataclass(frozen=True)
class CancelResult:
    job: JobRecord
    cancelled: bool
    """The job ended ``cancelled`` (False: it had already finished, or its owner has not acknowledged yet)."""
    requested: bool = False
    """The job belongs to another Farm process, which was asked to stop it."""


class JobManager:
    """Queue, limits and runner of the AI jobs of one Farm process (see the module docstring)."""

    def __init__(
        self,
        ctx: FarmContext,
        *,
        instance_id: UUID | None = None,
        heartbeat_s: float = HEARTBEAT_S,
        lease_s: float = LEASE_S,
        tick_s: float = TICK_S,
        busy_backoff_s: float = BUSY_BACKOFF_S,
        transport: Transport | None = None,
    ) -> None:
        self.ctx = ctx
        self.transport: Transport = transport or RouterTransport(ctx)
        self.instance_id = instance_id or uuid4()
        self.heartbeat_s = heartbeat_s
        self.lease_s = lease_s
        self.tick_s = tick_s
        self.busy_backoff_s = busy_backoff_s
        self.store = JobStore(ctx.pool)
        self.conversations = ConversationStore(ctx.pool)
        self._live: dict[UUID, _Work] = {}
        self._pending: list[_Work] = []
        self._active_by_account: dict[str, int] = {}
        self._active_by_ai: dict[str, int] = {}
        self._seq = 0
        self._ticker: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self._changed = asyncio.Event()
        self._closing = False
        self._last_sweep = float("-inf")

    @property
    def clock(self) -> Callable[[], datetime]:
        return self.ctx.clock

    # --- starting ---------------------------------------------------------------------------------------

    async def start(self, spec: AiJobSpec, *, caller: str) -> AiStarted:
        """One job (a first turn, or the next turn of ``spec.conversation_id``) queued; returns at once."""
        return (await self.start_many([spec], distinct_accounts=False, caller=caller))[0]

    async def start_many(
        self, specs: Sequence[AiJobSpec], *, distinct_accounts: bool, caller: str
    ) -> list[AiStarted]:
        """Queue every job or none: the accounts are planned first and a refusal starts nothing.

        ``distinct_accounts``: no two jobs on the same account; the call is refused, naming the accounts
        that are missing or unusable, when there are not enough.
        """
        if self._closing:
            raise AiRequestError("bad_request", "the Farm is shutting down and takes no new jobs")
        now = self.clock()
        accounts = await load_accounts(self.ctx.pool)
        conversations = [await self._conversation_for(spec) for spec in specs]
        wishes = [
            Wish(
                ai=spec.ai if conv is None else conv.ai,
                model=spec.model,
                account=spec.account if conv is None else conv.account,
            )
            for spec, conv in zip(specs, conversations, strict=True)
        ]
        try:
            plan = plan_accounts(accounts, wishes, distinct=distinct_accounts, now=now)
        except AiRequestError:
            # For one job, say why that job has no account rather than how many accounts are missing.
            if len(wishes) == 1:
                raise refuse_single(accounts, wishes[0], now) from None
            raise
        started: list[AiStarted] = []
        try:
            for spec, conv, account in zip(specs, conversations, plan, strict=True):
                started.append(await self._submit(spec, conv, account, caller))
        except BaseException:
            for done in started:
                await asyncio.shield(self._withdraw(done.job_id))
            raise
        return started

    async def reply(
        self, conversation_id: UUID, message: str, *, timeout_s: int | None, caller: str
    ) -> AiStarted:
        """The next turn of a conversation: same account and session; mode, cwd and model carry over."""
        conv = await self.conversations.get(conversation_id)
        if conv is None:
            raise AiRequestError("not_found", f"conversation {conversation_id} does not exist")
        if conv.native_session_id is None:
            raise AiRequestError(
                "bad_request",
                f"conversation {conv.id} has no native session yet (its first job did not succeed); "
                "start the work again with ai_start(conversation_id=...)",
                ai=conv.ai,
                account=conv.account,
            )
        last = None if conv.last_job_id is None else await self.store.get(conv.last_job_id)
        spec = AiJobSpec.model_validate(
            {
                "task": message,
                "conversation_id": str(conv.id),
                "mode": "answer" if last is None else last.mode,
                "cwd": None if last is None else last.cwd,
                "model": None if last is None else last.model,
                "timeout_s": timeout_s or (900 if last is None else last.timeout_s),
            }
        )
        return await self.start(spec, caller=caller)

    async def _conversation_for(self, spec: AiJobSpec) -> ConversationRecord | None:
        if spec.conversation_id is None:
            return None
        conv = await self.conversations.get(UUID(spec.conversation_id))
        if conv is None:
            raise AiRequestError("not_found", f"conversation {spec.conversation_id} does not exist")
        if spec.ai not in ("any", conv.ai):
            raise AiRequestError(
                "bad_request",
                f"conversation {conv.id} is a {conv.ai} conversation, not {spec.ai}",
                ai=conv.ai,
            )
        if spec.account not in (None, conv.account):
            raise AiRequestError(
                "bad_request",
                f"conversation {conv.id} lives on account '{conv.account}', not '{spec.account}'",
                ai=conv.ai,
                account=conv.account,
            )
        if spec.retry_other_account and conv.native_session_id is not None:
            raise AiRequestError(
                "bad_request",
                "retry_other_account is for first turns only: the session of this conversation lives on "
                f"account '{conv.account}'",
                ai=conv.ai,
                account=conv.account,
            )
        return conv

    async def _submit(
        self, spec: AiJobSpec, conv: ConversationRecord | None, account: AccountInfo, caller: str
    ) -> AiStarted:
        record = await self.store.create(
            conversation=conv,
            ai=account.ai,
            account=account.id,
            spec=spec,
            caller=caller,
            owner_id=self.instance_id,
            now=self.clock(),
        )
        self._seq += 1
        work = _Work(
            record=record,
            task=spec.task,
            seq=self._seq,
            ai=account.ai,
            account=account.id,
            limit=account.max_parallel,
            pool_limit=account.pool_max_parallel,
        )
        self._live[record.id] = work
        ahead = self._jobs_ahead(work)
        self._pending.append(work)
        self._ensure_ticker()
        self._pump()
        return AiStarted(
            job_id=record.id,
            conversation_id=record.conversation_id,
            account=record.account,
            ai=record.ai,
            model=record.model,
            turn=record.turn,
            state="queued",
            jobs_ahead=ahead,
        )

    async def _withdraw(self, job_id: UUID) -> None:
        """Take back a job of a batch that could not be queued completely."""
        try:
            await self.cancel(job_id)
        except Exception as exc:  # the batch's own error is what the caller must see
            log.error(
                "ai.withdraw_failed", job=str(job_id), error=f"{type(exc).__name__}: {redact(str(exc))}"
            )

    # --- the queue ----------------------------------------------------------------------------------------

    def _jobs_ahead(self, work: _Work) -> int:
        """How many jobs must finish on the account before ``work`` can start (0 = it starts now)."""
        earlier = sum(1 for w in self._pending if w.account == work.account and w.seq < work.seq)
        return max(0, self._active_by_account.get(work.account, 0) + earlier + 1 - work.limit)

    def _enqueue(self, work: _Work) -> None:
        """Put a job back in submission order (a retry or a busy account does not lose its place)."""
        index = next((i for i, w in enumerate(self._pending) if w.seq > work.seq), len(self._pending))
        self._pending.insert(index, work)

    def _pump(self) -> None:
        """Start every queued job whose account and pool have a free slot, oldest first."""
        now = time.monotonic()
        for work in list(self._pending):
            if work.not_before > now:
                continue
            if self._active_by_account.get(work.account, 0) >= work.limit:
                continue
            if work.pool_limit is not None and self._active_by_ai.get(work.ai, 0) >= work.pool_limit:
                continue
            self._pending.remove(work)
            self._launch(work)

    def _launch(self, work: _Work) -> None:
        work.slot = (work.account, work.ai)
        self._active_by_account[work.account] = self._active_by_account.get(work.account, 0) + 1
        self._active_by_ai[work.ai] = self._active_by_ai.get(work.ai, 0) + 1
        work.handle = asyncio.create_task(self._run(work), name=f"ai-job-{work.job_id}")

    def _release(self, work: _Work, *, requeued: bool) -> None:
        if work.slot is not None:
            account, ai = work.slot
            self._active_by_account[account] = max(0, self._active_by_account.get(account, 1) - 1)
            self._active_by_ai[ai] = max(0, self._active_by_ai.get(ai, 1) - 1)
            work.slot = None
        work.handle = None
        if requeued:
            self._enqueue(work)
        else:
            self._live.pop(work.job_id, None)
        self._notify()
        if not self._closing:
            self._pump()

    def _notify(self) -> None:
        old, self._changed = self._changed, asyncio.Event()
        old.set()

    async def _wait_change(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except TimeoutError:
            pass

    # --- running one job ----------------------------------------------------------------------------------

    async def _run(self, work: _Work) -> None:
        requeued = False
        try:
            record = await self.store.mark_running(work.job_id, self.instance_id, self.clock())
            if record is None:
                return  # cancelled or failed by someone else while it waited
            work.record = record
            log.info(
                "ai.job_started", job=str(work.job_id), ai=work.ai, account=work.account, turn=record.turn
            )
            work.attempt_started_at = self.clock()
            attempt = await self.transport.run(work.record, work.task)
            work.attempt_started_at = None
            requeued = await self._conclude(work, attempt)
        except asyncio.CancelledError:
            await asyncio.shield(self._conclude_interrupted(work))
            raise
        except Exception as exc:  # a bug: the job must not stay 'running' behind it
            log.error(
                "ai.job_crashed", job=str(work.job_id), error=f"{type(exc).__name__}: {redact(str(exc))}"
            )
            await asyncio.shield(self._conclude_crashed(work, exc))
        finally:
            self._release(work, requeued=requeued)

    def _log_attempt(self, work: _Work, attempt: Attempt) -> dict[str, Any]:
        failure = attempt.failure
        entry = AiAttempt(
            n=len(work.attempts) + 1,
            account=attempt.account,
            run_id=attempt.run_id,
            outcome="succeeded" if attempt.data is not None else "failed",
            kind=None if failure is None else failure.kind,
            message=None if failure is None else redact(failure.message)[:MAX_ERROR_CHARS],
            retry_at=attempt.retry_at,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            cost_usd=float(attempt.cost_usd),
        )
        return entry.model_dump(mode="json")

    async def _conclude(self, work: _Work, attempt: Attempt) -> bool:
        """Turn what the router said into the job's next state; True when the job went back to the queue."""
        now = time.monotonic()
        if attempt.busy:
            work.busy_since = work.busy_since or now
            if now - work.busy_since <= work.record.timeout_s:
                work.not_before = now + self.busy_backoff_s
                return await self._requeue(work, work.account)
            waited = int(now - work.busy_since)
            attempt = replace(
                attempt,
                busy=False,
                failure=Failure(
                    "account_unavailable",
                    f"account '{work.account}' stayed busy with other calls for {waited}s",
                    cause="busy",
                ),
            )
        work.busy_since = None
        work.attempts.append(self._log_attempt(work, attempt))
        work.cost += attempt.cost_usd
        work.tried.append(attempt.account)
        if attempt.data is not None:
            await self._finish_success(work, attempt)
            return False
        failure = attempt.failure
        if failure is None:  # unreachable: an attempt is an answer, a failure or busy
            failure = Failure("crash", "the attempt ended without a result")
        nxt = await self._next_account(work, failure)
        if nxt is not None:
            log.info(
                "ai.job_retry",
                job=str(work.job_id),
                failed_on=attempt.account,
                next=nxt.id,
                kind=failure.kind,
            )
            work.account, work.limit, work.pool_limit = nxt.id, nxt.max_parallel, nxt.pool_max_parallel
            return await self._requeue(work, nxt.id)
        await self._finish_failure(work, attempt, failure)
        return False

    async def _requeue(self, work: _Work, account: str) -> bool:
        return await self.store.requeue(
            work.record, self.instance_id, account=account, attempts=work.attempts, now=self.clock()
        )

    async def _next_account(self, work: _Work, failure: Failure) -> AccountInfo | None:
        record = work.record
        if not (
            record.retry_other_account
            and record.mode == "answer"
            and record.resume_session_id is None
            and retryable_on_another_account(failure.kind)
        ):
            return None
        accounts = await load_accounts(self.ctx.pool)
        free = usable(
            accounts, ai=work.ai, model=record.model, now=self.clock(), exclude=frozenset(work.tried)
        )
        return rank(free)[0] if free else None

    async def _finish_success(self, work: _Work, attempt: Attempt) -> None:
        data = attempt.data or {}
        text = str(data.get("text") or "")
        usage = {str(k): float(v) for k, v in (data.get("usage") or {}).items()}
        reported_cost = float(data.get("cost_usd") or 0.0) > 0
        stored = await asyncio.to_thread(results.store_result, work.job_id, text)
        record = work.record
        json_valid: bool | None = None
        json_errors: list[str] | None = None
        if record.json_schema is not None:
            checked = results.check_json(text, data.get("json"), record.json_schema)
            json_valid, json_errors = checked.valid, checked.errors
        files_changed = await results.git_status(record.cwd) if record.mode == "edit" and record.cwd else None
        fin = Finish(
            state="succeeded",
            account=attempt.account,
            attempts=work.attempts,
            run_id=attempt.run_id,
            cost_usd=work.cost,
            cost_estimated=not reported_cost,
            usage=usage,
            tokens=_tokens(usage),
            duration_s=float(data.get("duration_s") or 0.0)
            or (attempt.finished_at - attempt.started_at).total_seconds()
            or None,
            native_session_id=None if data.get("session_id") is None else str(data["session_id"]),
            stored=stored,
            json_valid=json_valid,
            json_errors=json_errors,
            files_changed=files_changed,
        )
        if await self._commit(record, fin):
            log.info("ai.job_finished", job=str(work.job_id), state="succeeded", account=attempt.account)
        else:
            await asyncio.to_thread(results.discard_result, stored.path)

    async def _commit(self, record: JobRecord, fin: Finish) -> bool:
        """Write a finished answer. A cancel that arrives while it is being written has lost the race: the
        write is let through, because the worker's answer was already complete and its quota already spent."""
        write = asyncio.ensure_future(self.store.finish(record, self.instance_id, fin, self.clock()))
        try:
            return await asyncio.shield(write)
        except asyncio.CancelledError:
            return await write

    async def _finish_failure(self, work: _Work, attempt: Attempt, failure: Failure) -> None:
        fin = Finish(
            state="failed",
            account=attempt.account,
            attempts=work.attempts,
            run_id=attempt.run_id,
            cost_usd=work.cost,
            failure=failure,
            retry_at=attempt.retry_at,
        )
        if await self.store.finish(work.record, self.instance_id, fin, self.clock()):
            log.info(
                "ai.job_finished",
                job=str(work.job_id),
                state="failed",
                kind=failure.kind,
                account=attempt.account,
            )

    async def _conclude_interrupted(self, work: _Work) -> None:
        """The runner was cancelled: by ``ai_cancel`` (cancelled) or because the Farm stops (farm_restart)."""
        by_caller = work.cancel_reason == "user"
        failure = (
            Failure("cancelled", "cancelled by the caller")
            if by_caller
            else Failure("farm_restart", FARM_RESTART_MESSAGE)
        )
        if work.attempt_started_at is not None:
            interrupted = Attempt(work.account, work.attempt_started_at, self.clock(), failure=failure)
            entry = self._log_attempt(work, interrupted)
            entry["outcome"] = "cancelled" if by_caller else "failed"
            work.attempts.append(entry)
            work.attempt_started_at = None
        fin = Finish(
            state="cancelled" if by_caller else "failed",
            account=work.account,
            attempts=work.attempts,
            cost_usd=work.cost,
            failure=failure,
        )
        if await self.store.finish(work.record, self.instance_id, fin, self.clock()):
            # The job owns no answer now: remove a file the interrupted write may have left.
            await asyncio.to_thread(results.discard_result, results.result_file(work.job_id))
            log.info("ai.job_finished", job=str(work.job_id), state=fin.state, kind=failure.kind)

    async def _conclude_crashed(self, work: _Work, exc: Exception) -> None:
        failure = Failure(
            "crash", f"the Farm hit an internal error: {type(exc).__name__}: {redact(str(exc))}"
        )
        fin = Finish(state="failed", account=work.account, attempts=work.attempts, failure=failure)
        try:
            await self.store.finish(work.record, self.instance_id, fin, self.clock())
        except psycopg.Error as db_exc:
            log.error("ai.job_unrecorded", job=str(work.job_id), error=type(db_exc).__name__)

    # --- reading ------------------------------------------------------------------------------------------

    async def status(self, job_ids: Sequence[UUID] | None) -> tuple[list[AiJobStatus], list[UUID]]:
        """Status of the named jobs (and which ids are unknown), or of the active and recent jobs."""
        await self._maybe_sweep()
        now = self.clock()
        if job_ids is None:
            records = await self.store.recent(since=now - STATUS_WINDOW, limit=STATUS_LIMIT)
            unknown: list[UUID] = []
        else:
            records = await self.store.get_many(job_ids)
            known = {r.id for r in records}
            unknown = [i for i in dict.fromkeys(job_ids) if i not in known]
        ahead = {w.job_id: self._jobs_ahead(w) for w in self._pending}
        return [status_of(r, now, ahead.get(r.id)) for r in records], unknown

    async def result(self, job_id: UUID, *, include_text: bool = True) -> AiJobResult | None:
        await self._maybe_sweep()
        record = await self.store.get(job_id)
        if record is None:
            return None
        return await result_of(record, self.clock(), include_text=include_text)

    async def wait(
        self,
        job_ids: Sequence[UUID],
        *,
        mode: Literal["any", "all"],
        timeout_s: float,
        include_text: bool = True,
    ) -> tuple[list[AiJobResult], list[AiJobStatus]]:
        """Block until one (``any``) or every (``all``) job finished or ``timeout_s`` passed.

        Returns the finished jobs with their results and the jobs still pending.
        """
        ids = list(dict.fromkeys(job_ids))
        deadline = time.monotonic() + timeout_s
        while True:
            await self._maybe_sweep()
            records = await self.store.get_many(ids)
            if len(records) != len(ids):
                known = {r.id for r in records}
                raise AiRequestError(
                    "not_found", "unknown job id(s): " + ", ".join(str(i) for i in ids if i not in known)
                )
            done = [r for r in records if r.finished]
            remaining = deadline - time.monotonic()
            if (mode == "any" and done) or len(done) == len(records) or remaining <= 0:
                break
            await self._wait_change(min(WAIT_POLL_S, remaining))
        now = self.clock()
        ahead = {w.job_id: self._jobs_ahead(w) for w in self._pending}
        finished = [await result_of(r, now, include_text=include_text) for r in done]
        pending = [status_of(r, now, ahead.get(r.id)) for r in records if not r.finished]
        return finished, pending

    # --- cancelling -------------------------------------------------------------------------------------

    async def cancel(self, job_id: UUID) -> CancelResult:
        """Stop a job: a queued one is withdrawn; a running one has its process tree killed, quota settled."""
        record = await self.store.get(job_id)
        if record is None:
            raise AiRequestError("not_found", f"job {job_id} does not exist")
        if record.finished:
            return CancelResult(record, cancelled=record.state == "cancelled")
        work = self._live.get(job_id)
        if work is None:
            requested = await self.store.request_cancel(job_id, self.clock())
            return await self._await_remote_cancel(requested or record)
        work.cancel_reason = "user"
        if work in self._pending:
            self._pending.remove(work)
            fin = Finish(
                state="cancelled",
                account=work.account,
                attempts=work.attempts,
                failure=Failure("cancelled", "cancelled by the caller before it started"),
            )
            await self.store.finish(work.record, self.instance_id, fin, self.clock())
            self._live.pop(job_id, None)
            self._notify()
        elif work.handle is not None:
            work.handle.cancel()
            await asyncio.wait({work.handle}, timeout=CANCEL_WAIT_S)
        latest = await self.store.get(job_id) or record
        return CancelResult(latest, cancelled=latest.state == "cancelled")

    async def _await_remote_cancel(self, record: JobRecord) -> CancelResult:
        """A job of another Farm process: it was asked to stop; wait a little for it to say so."""
        deadline = time.monotonic() + CANCEL_WAIT_S
        latest = record
        while time.monotonic() < deadline:
            await self._maybe_sweep()
            found = await self.store.get(record.id)
            latest = found or latest
            if latest.finished:
                break
            await asyncio.sleep(min(0.2, max(self.tick_s, 0.05)))
        return CancelResult(latest, cancelled=latest.state == "cancelled", requested=True)

    # --- heartbeat, cancel requests, orphans --------------------------------------------------------------

    def _ensure_ticker(self) -> None:
        if self._ticker is None or self._ticker.done():
            self._ticker = asyncio.create_task(self._tick_loop(), name="ai-job-ticker")

    async def _tick_loop(self) -> None:
        last_beat = time.monotonic()  # a job's row got its heartbeat when it was created
        while self._live and not self._closing:
            await asyncio.sleep(self.tick_s)
            try:
                self._pump()
                now = time.monotonic()
                if now - last_beat >= self.heartbeat_s:
                    last_beat = now
                    await self.store.heartbeat(self.instance_id, self.clock())
                for job_id in await self.store.cancel_requests(self.instance_id):
                    work = self._live.get(job_id)
                    if work is not None and work.cancel_reason is None:
                        self._spawn(self.cancel(job_id))
                await self._maybe_sweep()
            except Exception as exc:  # a failed tick must not end the heartbeat: the jobs would look orphaned
                log.error("ai.tick_failed", error=f"{type(exc).__name__}: {redact(str(exc))}")

    def _spawn(self, work: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.ensure_future(work)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _maybe_sweep(self) -> None:
        """Fail the jobs of dead Farm processes, at most every third of a lease."""
        now = time.monotonic()
        if now - self._last_sweep < self.lease_s / 3:
            return
        self._last_sweep = now
        await self.recover()

    async def recover(self) -> int:
        """Fail every job whose owner stopped renewing its heartbeat: ``farm_restart``, not re-run."""
        swept = await self.store.sweep_orphans(self.instance_id, self.clock(), self.lease_s)
        for record in swept:
            log.warning(
                "ai.job_orphaned", job=str(record.id), account=record.account, owner=str(record.owner_id)
            )
        if swept:
            self._notify()
        return len(swept)

    # --- stopping -----------------------------------------------------------------------------------------

    async def drain(self) -> None:
        """Wait until none of this process's jobs is queued or running (a graceful stop waits for work)."""
        while self._live:
            await self._wait_change(WAIT_POLL_S)

    async def shutdown(self) -> None:
        """Stop for good: queued and running jobs end ``farm_restart``; running ones lose their processes."""
        self._closing = True
        handles: list[asyncio.Task[None]] = []
        for work in list(self._pending):
            self._pending.remove(work)
            work.cancel_reason = "shutdown"
            await self._conclude_interrupted(work)
            self._live.pop(work.job_id, None)
        for work in list(self._live.values()):
            work.cancel_reason = "shutdown"
            if work.handle is not None:
                work.handle.cancel()
                handles.append(work.handle)
        if handles:
            await asyncio.gather(*handles, return_exceptions=True)
        for task in [self._ticker, *self._background]:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *[t for t in [self._ticker, *self._background] if t is not None], return_exceptions=True
        )
        self._notify()


def _tokens(usage: dict[str, float]) -> int | None:
    """Total tokens of a call: the CLI's own total, else input + output (+ reasoning)."""
    if not usage:
        return None
    total = usage.get("total_tokens")
    if total is None:
        total = sum(usage.get(k, 0.0) for k in ("input_tokens", "output_tokens", "reasoning_tokens"))
    return int(total)
