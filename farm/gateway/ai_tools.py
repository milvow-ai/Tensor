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
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import ToolResult
from pydantic import Field, ValidationError

from farm.ai import selection
from farm.ai.accounts import ai_options as options_of
from farm.ai.accounts import list_ai_accounts, load_accounts, options_menu
from farm.ai.failures import AiRequestError
from farm.ai.jobs import JobManager
from farm.capabilities.schemas import (
    ACCOUNT_SLUG,
    EFFORT_DESCRIPTION,
    MAX_BATCH_JOBS,
    MAX_JOB_TIMEOUT_S,
    MAX_TASK_CHARS,
    MODEL_DESCRIPTION,
    USE_DEFAULTS_DESCRIPTION,
    AiEffort,
    AiJobSpec,
    AiMode,
    AiProvider,
    AiRequestFailure,
    parse_uuid,
)
from farm.context import FarmContext
from farm.db.pool import tenant_connection
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.gateway.middleware import IDENTITY_SCOPE_KEY, current_caller, current_identity
from farm.resources.router import route
from farm.tenancy import TokenIdentity

log = structlog.get_logger(__name__)

AI_TOOLS = (
    "list_ais",
    "ai_options",
    "ai_refresh_models",
    "ai_usage_observations",
    "ask_ai_batch",
    "ai_start",
    "ai_start_many",
    "ai_status",
    "ai_wait",
    "ai_result",
    "ai_cancel",
    "ai_reply",
    "ai_recover",
    "ai_attention",
    "ai_conversations",
)
MAX_WAIT_S = 300
MAX_IDS = 100
RECOVERY_APPROVAL_SCOPE = "ai:recovery:approve"


def _recovery_approval_actor() -> tuple[bool, str]:
    """Whether this call may approve a provider/model/edit retry, and who to record as the approver.

    Only the local stdio boundary (no HTTP request at all) is trusted administration. An HTTP call whose
    token identity was not bound fails closed, whichever middleware stack served it.
    """
    identity = current_identity.get()
    if identity is None:
        try:
            scoped = get_http_request().scope.get(IDENTITY_SCOPE_KEY)
        except RuntimeError:
            return True, f"local:{current_caller.get()}"
        if not isinstance(scoped, TokenIdentity):
            return False, f"http:{current_caller.get()}"
        identity = scoped
    allowed = "*" in identity.scopes or RECOVERY_APPROVAL_SCOPE in identity.scopes
    return allowed, f"token:{identity.token_id}:{identity.name}"


def _ok(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(structured_content={"ok": True, **payload})


def _refused(error: AiRequestError) -> ToolResult:
    failure = AiRequestFailure(
        kind=error.kind, message=error.message, ai=error.ai, account=error.account, retry_at=error.retry_at
    )
    content: dict[str, Any] = {"ok": False, "error": failure.model_dump(mode="json")}
    if error.choices is not None:
        content["choices"] = error.choices
    return ToolResult(structured_content=content, is_error=True)


async def ask_ai_selection_gate(ctx: FarmContext, args: dict[str, Any]) -> ToolResult | None:
    """The blocking ``ask_ai``: a call without an explicit choice (and no ``use_defaults``) is answered with
    ``selection_required`` and the menu, before the router runs (no run, quota or CLI). ``None``: go on; the
    router still validates the choice against every candidate it considers."""
    if args.get("use_defaults") is True:
        return None
    ai = args.get("ai") or "any"
    if not isinstance(ai, str):
        return None  # the router refuses a malformed input with the schema's own message
    model, effort = args.get("model"), args.get("effort")
    missing = selection.missing_fields(
        ai,
        model=model if isinstance(model, str) else None,
        effort=effort if isinstance(effort, str) else None,
    )
    if not missing:
        return None
    message = f"choose {' and '.join(missing)} before starting"
    choices = [
        {
            "index": 0,
            "kind": "selection_required",
            "message": message,
            "missing": list(missing),
            "options": options_menu(await load_accounts(ctx.pool), ctx.clock(), ai=ai),
        }
    ]
    error = {
        "kind": "selection_required",
        "message": f"{message} (see choices)",
        "hint": "call again with model (and effort where supported) from choices, or use_defaults=true",
        "attempts": [],
    }
    return ToolResult(structured_content={"ok": False, "error": error, "choices": choices}, is_error=True)


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
        """List the AIs (usable / total accounts, jobs) and each account once: usable now or, if not, why
        (`unusable`) and when it is back (`back_at`); status, login state, models, efforts, a limit reset
        still ahead (`reset_at`), calls today, active and queued jobs, max_parallel, and the last error as
        {kind, at, message} (one short line; a success clears it)."""
        return await list_ai_accounts(ctx.pool, ctx.clock())

    @server.tool(annotations={"readOnlyHint": True})
    async def ai_refresh_models(
        account: Annotated[str, Field(pattern=ACCOUNT_SLUG)], retry_model: str | None = None
    ) -> dict[str, Any]:
        """Refresh an account's provider model listing without sending an inference prompt.

        Available only for supported metadata adapters. Access remains unknown until a real requested
        job succeeds; a plan label never proves access. retry_model explicitly clears a prior rejection
        so the next requested job can recheck it after an account upgrade; this does not assert access.
        """
        from farm.connect.service import get_login_service

        try:
            return {"account": account, **await get_login_service().refresh_models(account, retry_model)}
        except NotImplementedError:
            return {"account": account, "status": "unsupported", "access": "unknown"}

    @server.tool(annotations={"readOnlyHint": True})
    async def ai_usage_observations(
        account: Annotated[str | None, Field(pattern=ACCOUNT_SLUG)] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Inspect recorded Farm jobs with their session, model/effort and metering coverage.

        Account quota windows are provider observations; overlapping jobs cannot be assigned a quota
        percentage. Missing usage/cost stays unknown. These records do not predict how many jobs remain.
        """
        from farm.manager.ai_usage import usage_report

        async with tenant_connection(ctx.pool) as conn:
            cur = await conn.execute(
                "select id,account,native_session_id,model,resolved_model,effort,effective_effort,"
                "state,finished_at,usage,cost_usd,cost_estimated from public.ai_jobs "
                "where (%s::text is null or account=%s) and finished_at is not null "
                "order by finished_at desc,id limit %s",
                (account, account, limit),
            )
            rows = await cur.fetchall()
        jobs = [
            {
                "job_id": str(jid),
                "account": aid,
                "session_id": session,
                "requested_model": model,
                "resolved_model": resolved,
                "requested_effort": effort,
                "effective_effort": effective,
                "state": state,
                "finished_at": at.isoformat(),
                "usage": usage or None,
                "usage_scope": "runner_reported_for_job_or_session" if usage else "unknown",
                "token_coverage": "reported" if usage else "unknown",
                "cost_usd": float(cost) if cost and not estimated else None,
                "cost_coverage": "reported" if cost and not estimated else "unknown_or_estimated",
                "quota_percentage_attribution": "unknown",
            }
            for (
                jid,
                aid,
                session,
                model,
                resolved,
                effort,
                effective,
                state,
                at,
                usage,
                cost,
                estimated,
            ) in rows
        ]
        allowances = (await usage_report(ctx.pool, ctx.clock()))["provider_allowances"]
        return {
            "scope": "farm_observed_jobs",
            "jobs": jobs,
            "provider_allowances": [
                a for a in allowances if account is None or a["connection_id"] == account
            ],
            "remaining_jobs_estimate": None,
            "estimate_status": "unknown",
            "quota_scope": "account_windows_not_per_job",
        }

    @server.tool(annotations={"readOnlyHint": True})
    async def ai_options(
        ai: Annotated[str | None, Field(pattern=ACCOUNT_SLUG, description="Only this AI.")] = None,
        account: Annotated[str | None, Field(pattern=ACCOUNT_SLUG, description="Only this account.")] = None,
    ) -> ToolResult:
        """The menu to pick from before starting AI work: per AI whether effort applies, per account (labelled
        provider · account) the configured models, provider listing and observed-access status, the efforts
        (or not_supported, with what Codex applies for xhigh/max) and the opt-in default policy.
        ai_start / ai_start_many / ask_ai refuse a job without model (and effort where supported) unless
        use_defaults=true and the account's defaults are valid. Read-only."""
        return _ok(await options_of(ctx.pool, ctx.clock(), ai=ai, account=account))

    @server.tool()
    async def ask_ai_batch(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """Run several AI tasks across the AI pool and wait for all answers (blocking, short tasks).

        Each task needs model (and effort where supported; see ai_options) or use_defaults=true.
        For long work or follow-ups use ai_start_many / ai_start instead: they return at once.
        """

        async def run_one(task_args: dict[str, Any]) -> dict[str, Any]:
            args = dict(task_args)
            strategy = args.pop(routing_argument, None)
            refused = await ask_ai_selection_gate(ctx, args)
            if refused is not None:
                return dict(refused.structured_content or {})
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
        model: Annotated[str | None, Field(description=MODEL_DESCRIPTION)] = None,
        effort: Annotated[AiEffort | None, Field(description=EFFORT_DESCRIPTION)] = None,
        use_defaults: Annotated[bool, Field(description=USE_DEFAULTS_DESCRIPTION)] = False,
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
            bool | None,
            Field(
                description="Automatic failover. Default: on for answer mode, off for edit (an edit is never "
                "replayed automatically). On a limit, a logout or a crash of the account the job continues "
                "on an untried account of the same AI and model in a fresh session, with saved history and "
                "partial output as context (same job_id, at most 5 attempts). false opts out."
            ),
        ] = None,
    ) -> ToolResult:
        """Give a task to another AI and return at once with {job_id, conversation_id, account, model,
        effort, effective_effort, selection_source}.

        Pick model and effort from ai_options first: without them (and without use_defaults) nothing starts
        and the reply is selection_required with the choices. A conversation_id continues with its persisted
        choice. The job runs in the background (it survives this client disconnecting) on one account of the
        AI. Check it with ai_status / ai_wait, read the exact answer with ai_result, send a follow-up to the
        same worker with ai_reply. A job waits when its account is busy (one job per account unless the
        account allows more).
        """
        try:
            spec = AiJobSpec(
                task=task,
                ai=ai,
                account=account,
                model=model,
                effort=effort,
                use_defaults=use_defaults,
                mode=mode,
                cwd=cwd,
                conversation_id=conversation_id,
                json_schema=json_schema,
                timeout_s=timeout_s,
                retry_other_account=mode == "answer" if retry_other_account is None else retry_other_account,
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
        or unusable. Every job needs model (and effort where supported) or use_defaults; one job without a
        valid choice refuses the whole call (selection_required / invalid_selection, choices per job index).
        Returns {jobs: [{job_id, conversation_id, account, model, effort, ...}, ...]} in the order given.
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
    async def ai_attention(
        limit: Annotated[int, Field(ge=1, le=MAX_IDS, description="Most failed jobs to list.")] = 20,
    ) -> ToolResult:
        """Failed jobs that need an operator, newest first: account, failure kind and the recovery hint
        (recovery.action: recover_same_provider, reconcile_then_recover, operator_approval_required or
        manual_review). Read-only: nothing is retried or acknowledged."""
        jobs = await mgr.attention(limit)
        return _ok({"jobs": [s.model_dump(mode="json") for s in jobs]})

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
    async def ai_recover(
        job_id: Annotated[
            str, Field(description="A failed job to continue using its saved input and history.")
        ],
        ai: AiProvider | None = None,
        account: Annotated[str | None, Field(pattern=ACCOUNT_SLUG)] = None,
        model: str | None = None,
        effort: Annotated[
            AiEffort | None, Field(description="Default: the failed job's effort (same provider only).")
        ] = None,
        approve_provider_change: bool = False,
        approve_model_change: bool = False,
        acknowledge_reconciliation: bool = False,
    ) -> ToolResult:
        """Continue a failed job once on an untried account; default is the same provider.

        Inputs/history are durable. Hidden native state and unsaved partial output cannot transfer.
        Cross-provider continuation requires approve_provider_change. Edits require inspecting existing
        files/external effects and acknowledge_reconciliation. Never automatically replay an ambiguous edit.
        A competing recovery returns recovery_exists and its child job ID. Maximum five recovery steps.
        """
        try:
            (parsed,) = _uuids([job_id], "job_id")
            authorized, actor = _recovery_approval_actor()
            started = await mgr.recover_job(
                parsed,
                caller=current_caller.get(),
                approval_authorized=authorized,
                approval_actor=actor,
                ai=ai,
                account=account,
                model=model,
                effort=effort,
                approve_provider_change=approve_provider_change,
                approve_model_change=approve_model_change,
                acknowledge_reconciliation=acknowledge_reconciliation,
            )
        except ValidationError as exc:
            return _refused(AiRequestError("bad_request", _problems(exc)))
        except AiRequestError as exc:
            return _refused(exc)
        return _ok(started.model_dump(mode="json"))

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
        effort: Annotated[
            AiEffort | None,
            Field(description="Effort the account offers. Default: the conversation's persisted one."),
        ] = None,
        model: Annotated[
            str | None,
            Field(
                description="Only for a conversation from before the explicit choice (no persisted model); "
                "a conversation never changes model."
            ),
        ] = None,
        retry_other_account: Annotated[
            bool | None,
            Field(
                description="Automatic failover for this turn. Default: as the conversation's last job "
                "(answer mode only). false opts out."
            ),
        ] = None,
    ) -> ToolResult:
        """Send a follow-up to the same worker: a new job on the same account and native session.

        The worker remembers the conversation. When that account is limited, logged out or crashes and
        failover is on (answer mode), the turn continues on an untried account of the same AI in a fresh
        session that gets the saved history as context; otherwise it fails with account_unavailable and
        retry_at. The provider or model never changes automatically. One turn at a time.
        """
        try:
            (parsed,) = _uuids([conversation_id], "conversation_id")
            started = await mgr.reply(
                parsed,
                message,
                timeout_s=timeout_s,
                effort=effort,
                model=model,
                retry_other_account=retry_other_account,
                caller=current_caller.get(),
            )
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
