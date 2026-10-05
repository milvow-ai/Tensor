"""The AI tools of the Farm MCP server.

Blocking (M3e-B): ``ask_ai`` (generated from the capability table, see ``server.py``), ``ask_ai_batch``,
``list_ais``. Non-blocking (AIP2), for a main AI that runs other AIs as workers::

    ai_start / ai_start_many  -> job ids at once (account chosen, queued per account)
    ai_status / ai_wait       -> which jobs run, queue or finished
    ai_result                 -> the worker's exact final text, usage, cost, files changed, or why it failed
    ai_reply                  -> a follow-up to the same worker (same account, same native session)
    ai_cancel / ai_conversations

Jobs run inside the Farm process (``farm.ai.jobs``), so they go on when the calling client disconnects. A
request the Farm refuses before any worker runs (unknown id, no usable account, busy conversation) and a job
that failed are both ``ok = false`` with ``error.kind`` and the account, so the caller knows which worker
failed and why.

MCP background tasks (SEP-2663) are not used: they need the optional ``fastmcp-tasks`` package, and the
explicit tools work for every client.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Annotated, Any, Literal
from uuid import UUID

import structlog
from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from pydantic import Field, ValidationError

from farm.ai.accounts import list_ai_accounts
from farm.ai.failures import AiRequestError
from farm.ai.jobs import JobManager
from farm.capabilities.schemas import (
    ACCOUNT_SLUG,
    MAX_BATCH_JOBS,
    MAX_JOB_TIMEOUT_S,
    MAX_TASK_CHARS,
    AiJobSpec,
    AiMode,
    AiProvider,
    AiRequestFailure,
    parse_uuid,
)
from farm.context import FarmContext
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.gateway.middleware import current_caller
from farm.resources.router import route

log = structlog.get_logger(__name__)

AI_TOOLS = (
    "list_ais",
    "ask_ai_batch",
    "ai_start",
    "ai_start_many",
    "ai_status",
    "ai_wait",
    "ai_result",
    "ai_cancel",
    "ai_reply",
    "ai_conversations",
)
MAX_WAIT_S = 300
MAX_IDS = 100


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(structured_content={"ok": True, **payload})


def _refused(error: AiRequestError) -> ToolResult:
    failure = AiRequestFailure(
        kind=error.kind, message=error.message, ai=error.ai, account=error.account, retry_at=error.retry_at
    )
    return ToolResult(
        structured_content={"ok": False, "error": failure.model_dump(mode="json")}, is_error=True
    )


def _problems(exc: ValidationError) -> str:
    """The reasons an input was refused, without echoing the input (a task can be long, or private)."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}"
        for e in exc.errors(include_input=False, include_url=False)
    )


def _uuids(values: Sequence[str], what: str) -> list[UUID]:
    try:
        return [parse_uuid(v, what) for v in values]
    except ValueError as exc:
        raise AiRequestError("bad_request", str(exc)) from None


class ManagerHook:
    """What ``FarmContext.aclose`` closes: it calls ``aclose`` on every registered executor that has one.

    Registering the job manager there ties its lifetime to the context's: when the Farm stops, running jobs
    lose their process trees and end ``farm_restart`` before the database pool closes.
    """

    def __init__(self, manager: JobManager) -> None:
        self.manager = manager

    async def execute(self, req: ExecRequest) -> ExecResult:
        return ExecResult(
            ok=False,
            error_kind=ErrorKind.BAD_REQUEST,
            error="the AI job manager is not a provider executor",
        )

    async def aclose(self) -> None:
        await self.manager.shutdown()


async def register_ai_tools(
    server: FastMCP, ctx: FarmContext, *, routing_argument: str, manager: JobManager | None = None
) -> JobManager:
    """Add the AI tools to ``server`` and return the job manager behind them.

    Also fails the jobs of Farm processes that died (``farm_restart``) and hands the manager to the context
    for shutdown. ``manager``: for tests that need other timings than the defaults.
    """
    mgr = manager or JobManager(ctx)
    ctx.executors[f"ai-jobs:{mgr.instance_id}"] = ManagerHook(mgr)
    swept = await mgr.recover()
    if swept:
        log.warning("ai.jobs_failed_at_startup", count=swept, kind="farm_restart")

    @server.tool(annotations={"readOnlyHint": True})
    async def list_ais() -> dict[str, Any]:
        """List the AIs and their accounts: status, login state, models, limit/reset time, calls today,
        active and queued jobs and the parallel jobs each account takes (max_parallel)."""
        return await list_ai_accounts(ctx.pool, ctx.clock())

    @server.tool()
    async def ask_ai_batch(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """Run several AI tasks across the AI pool and wait for all answers (blocking, short tasks).

        For long work or follow-ups use ai_start_many / ai_start instead: they return at once.
        """

        async def run_one(task_args: dict[str, Any]) -> dict[str, Any]:
            args = dict(task_args)
            strategy = args.pop(routing_argument, None)
            outcome = await route(ctx, "ask_ai", args, strategy=strategy, caller=current_caller.get())
            return outcome.envelope()

        envelopes = await asyncio.gather(*(run_one(t) for t in tasks))
        all_ok = all(e.get("ok", False) for e in envelopes)
        return {"ok": all_ok, "tasks": list(envelopes)}

    @server.tool()
    async def ai_start(
        task: Annotated[
            str, Field(min_length=1, max_length=MAX_TASK_CHARS, description="What the worker must do.")
        ],
        ai: Annotated[
            AiProvider,
            Field(description="claude, codex, gemini, hermes, or any (the first AI with a usable account)."),
        ] = "any",
        account: Annotated[
            str | None,
            Field(
                pattern=ACCOUNT_SLUG,
                description="Run on exactly this account, e.g. claude-03. Default: the Farm picks.",
            ),
        ] = None,
        model: Annotated[str | None, Field(description="Model name; the account must offer it.")] = None,
        mode: Annotated[
            AiMode,
            Field(
                description="answer = read-only; edit = may change files under cwd (if the account allows)."
            ),
        ] = "answer",
        cwd: Annotated[str | None, Field(description="Working directory (required for mode edit).")] = None,
        conversation_id: Annotated[
            str | None, Field(description="Continue this conversation (same account, same native session).")
        ] = None,
        json_schema: Annotated[
            dict[str, Any] | None,
            Field(description="Ask for JSON matching this schema; ai_result reports validation errors."),
        ] = None,
        timeout_s: Annotated[
            int, Field(ge=1, le=MAX_JOB_TIMEOUT_S, description="Longest the worker may run, in seconds.")
        ] = 900,
        retry_other_account: Annotated[
            bool,
            Field(
                description="First turn, answer mode only: on a limit, a logout or a crash of the account, "
                "rerun on the next account."
            ),
        ] = False,
    ) -> ToolResult:
        """Give a task to another AI and return at once with {job_id, conversation_id, account}.

        The job runs in the background (it survives this client disconnecting) on one account of the AI.
        Check it with ai_status / ai_wait, read the exact answer with ai_result, send a follow-up to the same
        worker with ai_reply. A job waits when its account is busy (one job per account unless the account
        allows more).
        """
        try:
            spec = AiJobSpec(
                task=task,
                ai=ai,
                account=account,
                model=model,
                mode=mode,
                cwd=cwd,
                conversation_id=conversation_id,
                json_schema=json_schema,
                timeout_s=timeout_s,
                retry_other_account=retry_other_account,
            )
            started = await mgr.start(spec, caller=current_caller.get())
        except ValidationError as exc:
            return _refused(AiRequestError("bad_request", _problems(exc)))
        except AiRequestError as exc:
            return _refused(exc)
        return _ok(started.model_dump(mode="json"))

    @server.tool()
    async def ai_start_many(
        jobs: Annotated[
            list[AiJobSpec],
            Field(min_length=1, max_length=MAX_BATCH_JOBS, description="The jobs, each as for ai_start."),
        ],
        distinct_accounts: Annotated[
            bool,
            Field(
                description="Give every job a different account; refuse the whole call if there are too few."
            ),
        ] = True,
    ) -> ToolResult:
        """Start several jobs at once, e.g. the same task on 3 Claude accounts and 2 Gemini.

        With distinct_accounts every job gets its own account, so they run in parallel; when there are not
        enough usable accounts of an AI nothing is started and the error names the accounts that are missing
        or unusable. Returns {jobs: [{job_id, conversation_id, account}, ...]} in the order given.
        """
        try:
            started = await mgr.start_many(
                jobs, distinct_accounts=distinct_accounts, caller=current_caller.get()
            )
        except AiRequestError as exc:
            return _refused(exc)
        return _ok({"jobs": [s.model_dump(mode="json") for s in started]})

    @server.tool(annotations={"readOnlyHint": True})
    async def ai_status(
        job_ids: Annotated[
            list[str] | None,
            Field(
                max_length=MAX_IDS, description="Jobs to report; default: queued, running and recent ones."
            ),
        ] = None,
    ) -> ToolResult:
        """State of jobs: queued, running, succeeded, failed or cancelled, with account, elapsed time, the
        jobs ahead of a queued one, tokens (once finished) and the failure kind. No answer text: that is
        ai_result."""
        try:
            ids = None if job_ids is None else _uuids(job_ids, "job_id")
            statuses, unknown = await mgr.status(ids)
        except AiRequestError as exc:
            return _refused(exc)
        return _ok(
            {"jobs": [s.model_dump(mode="json") for s in statuses], "unknown": [str(u) for u in unknown]}
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def ai_wait(
        job_ids: Annotated[
            list[str], Field(min_length=1, max_length=MAX_IDS, description="The jobs to wait for.")
        ],
        mode: Annotated[
            Literal["any", "all"],
            Field(description="any = return when one finished; all = when every one did."),
        ] = "all",
        timeout_s: Annotated[
            float, Field(ge=0, le=MAX_WAIT_S, description="Longest to wait, in seconds (at most 300).")
        ] = 30,
        include_text: Annotated[
            bool, Field(description="false = leave the answer text out (fetch it with ai_result).")
        ] = True,
    ) -> ToolResult:
        """Wait for jobs and return the finished ones with their exact results; call again for the rest.

        Returns {finished: [...], pending: [...], timed_out}. A finished entry is the same as ai_result: ok,
        the worker's text, usage, cost, or the failure (kind, account, message, retry_at).
        """
        try:
            finished, pending = await mgr.wait(
                _uuids(job_ids, "job_id"), mode=mode, timeout_s=timeout_s, include_text=include_text
            )
        except AiRequestError as exc:
            return _refused(exc)
        timed_out = bool(pending) and not (mode == "any" and finished)
        return _ok(
            {
                "finished": [r.model_dump(mode="json", by_alias=True) for r in finished],
                "pending": [p.model_dump(mode="json") for p in pending],
                "timed_out": timed_out,
            }
        )

    @server.tool(annotations={"readOnlyHint": True})
    async def ai_result(job_id: Annotated[str, Field(description="A job id from ai_start.")]) -> ToolResult:
        """The exact result of a finished job: the worker's final text unmodified (a result over the inline
        limit comes as a file path plus the first 20,000 characters), parsed JSON when a json_schema was given
        (validation errors reported, raw text kept), the native session id, model, usage, cost, duration and,
        for an edit, the files changed. A failed job returns ok = false with {kind, ai, account, message,
        retry_at}: kind is limit, auth, timeout, crash, bad_request, cancelled, farm_restart or
        account_unavailable."""
        try:
            (parsed,) = _uuids([job_id], "job_id")
            result = await mgr.result(parsed)
            if result is None:
                raise AiRequestError("not_found", f"job {job_id} does not exist")
            if result.state in ("queued", "running"):
                raise AiRequestError(
                    "not_finished",
                    f"job {job_id} is {result.state}; wait for it with ai_wait",
                    account=result.account,
                )
        except AiRequestError as exc:
            return _refused(exc)
        return ToolResult(
            structured_content=result.model_dump(mode="json", by_alias=True), is_error=not result.ok
        )

    @server.tool()
    async def ai_cancel(job_id: Annotated[str, Field(description="A job id from ai_start.")]) -> ToolResult:
        """Stop a job: a queued job is withdrawn; a running job's process tree is killed, its quota settled.

        A job that already finished is reported as it is.
        """
        try:
            (parsed,) = _uuids([job_id], "job_id")
            outcome = await mgr.cancel(parsed)
        except AiRequestError as exc:
            return _refused(exc)
        note = None
        if outcome.requested and not outcome.cancelled:
            note = "cancel requested; the Farm process that owns the job has not stopped it yet"
        elif not outcome.cancelled:
            note = f"the job had already finished ({outcome.job.state})"
        return _ok(
            {
                "job_id": str(outcome.job.id),
                "state": outcome.job.state,
                "cancelled": outcome.cancelled,
                "message": note,
            }
        )

    @server.tool()
    async def ai_reply(
        conversation_id: Annotated[
            str, Field(description="conversation_id from ai_start / ai_conversations.")
        ],
        message: Annotated[
            str, Field(min_length=1, max_length=MAX_TASK_CHARS, description="The follow-up for the worker.")
        ],
        timeout_s: Annotated[
            int | None, Field(ge=1, le=MAX_JOB_TIMEOUT_S, description="Default: the last job's timeout.")
        ] = None,
    ) -> ToolResult:
        """Send a follow-up to the same worker: a new job on the same account and native session.

        The worker remembers the conversation. Fails with account_unavailable and retry_at when that account
        is limited or logged out: a conversation never moves to another account. One turn at a time.
        """
        try:
            (parsed,) = _uuids([conversation_id], "conversation_id")
            started = await mgr.reply(parsed, message, timeout_s=timeout_s, caller=current_caller.get())
        except ValidationError as exc:
            return _refused(AiRequestError("bad_request", _problems(exc)))
        except AiRequestError as exc:
            return _refused(exc)
        return _ok(started.model_dump(mode="json"))

    @server.tool(annotations={"readOnlyHint": True})
    async def ai_conversations(
        ai: Annotated[str | None, Field(pattern=ACCOUNT_SLUG, description="Only this AI.")] = None,
        account: Annotated[str | None, Field(pattern=ACCOUNT_SLUG, description="Only this account.")] = None,
        only_active: Annotated[
            bool, Field(description="Only conversations with a queued or running job.")
        ] = False,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> ToolResult:
        """The open conversations (threads with one worker): account, native session, turns, tokens, cost, the
        last job and its state. Use a conversation_id with ai_reply."""
        found = await mgr.conversations.threads(ai=ai, account=account, only_active=only_active, limit=limit)
        return _ok({"conversations": [c.model_dump(mode="json") for c in found]})

    return mgr
