"""Durable tasks shared by a member's MCP clients."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import structlog
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_request
from psycopg.rows import dict_row
from pydantic import BaseModel

from farm.ai import completions
from farm.ai.accounts import list_ai_accounts
from farm.ai.failures import AiRequestError
from farm.ai.jobs import JobManager
from farm.capabilities.schemas import AiJobSpec
from farm.context import FarmContext
from farm.control.task_handoffs import (
    Actor,
    Approach,
    Reservation,
    Resolution,
    TaskError,
    TaskStore,
    TerminalState,
    workspace_path,
)
from farm.db.pool import tenant_connection
from farm.executors.base import ExecRequest, ExecResult
from farm.executors.mcp.client import REJECTION_KEY, RESULT_KEY
from farm.gateway.mcp_tools import PassthroughTool
from farm.gateway.middleware import (
    PolicyCheck,
    current_caller,
    current_client,
    current_identity,
    tool_scope_allows,
)
from farm.manager.ai_usage import usage_report
from farm.resources.reports import capacity_report
from farm.resources.router import RouteOutcome
from farm.resources.trajectory import to_jsonable
from farm.secrets import redact
from farm.tenancy.context import current_user, require_tenant, use_tenant

TASK_TOOLS = (
    "task_open", "task_list", "task_status", "task_takeover", "task_checkpoint", "task_local_edit",
    "task_cli", "task_call", "task_resolve_step", "task_complete", "worker_profiles",
)
BUCKET: completions.Bucket = "task-completions"
MAX_RECEIPT_BYTES = 650_000
MAX_DISPATCHING = 64
log = structlog.get_logger(__name__)


def _actor() -> Actor:
    identity = current_identity.get()
    caller = current_caller.get()
    if identity is not None and identity.user_id is not None and identity.user_id == current_user.get():
        client = current_client.get()
        label = identity.name if client == "mcp" else f"{identity.name} · {client.removeprefix('mcp:')}"
        return Actor(identity.user_id, f"{identity.token_id}:{client}", label)
    try:
        get_http_request()
    except RuntimeError:
        if identity is None:
            return Actor(current_user.get(), f"local:{caller}", caller)
    raise ToolError("task tools require an MCP token issued to a Farm member")


def _allow(policy: PolicyCheck | None, tool: str, args: dict[str, Any]) -> None:
    identity = current_identity.get()
    if identity is not None and not tool_scope_allows(identity.scopes, tool):
        raise ToolError("policy: token scope does not allow the target tool")
    if policy is not None and (reason := policy(current_caller.get(), tool, args)) is not None:
        raise ToolError(f"policy: {reason}")


async def _view(work: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], to_jsonable(await work, None))
    except TaskError as exc:
        raise ToolError(str(exc)) from None


def _checkout(cwd: str | None) -> str:
    if not cwd or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise ToolError("an edit job needs an existing absolute cwd on the worker host")
    folder = Path(cwd).resolve()
    for parent in (folder, *folder.parents):
        if (parent / ".git").exists():
            return workspace_path(str(parent))
    return workspace_path(str(folder))


class Receipt(BaseModel):
    user_id: UUID | None
    state: TerminalState
    result: dict[str, Any]


def _receipt_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value).replace("\x00", "�").encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, dict):
        return {_receipt_value(str(key)): _receipt_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_receipt_value(item) for item in value]
    return to_jsonable(value, None)


class TaskDispatch:
    """Keep accepted submissions alive after disconnect and replay terminal writes after DB failure."""

    def __init__(self, ctx: FarmContext, store: TaskStore) -> None:
        self.ctx, self.store = ctx, store
        self.replay_lock = asyncio.Lock()
        self.retry_task: asyncio.Task[None] | None = None

    def check_capacity(self) -> None:
        if sum(key.startswith("task:") for key in self.ctx.flights) >= MAX_DISPATCHING:
            raise ToolError("too many submissions in flight; try again")

    async def submit(
        self, reserve: Callable[[], Awaitable[Reservation]], work: Callable[[UUID], Awaitable[None]],
        *, wait_for_start: bool = False,
    ) -> None:
        self.check_capacity()
        key = f"task:{uuid4()}"
        accepted: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def execute() -> None:
            try:
                reservation = await reserve()
                if not wait_for_start:
                    accepted.set_result(None)
                if reservation.fresh:
                    await work(reservation.id)
            except Exception as exc:
                if accepted.done():
                    raise
                accepted.set_exception(exc)
                return
            if not accepted.done():
                accepted.set_result(None)

        task = asyncio.create_task(execute())
        self.ctx.flights[key] = task

        def settled(done: asyncio.Task[None]) -> None:
            self.ctx.flights.pop(key, None)
            if not accepted.done():
                accepted.cancel()
            if not done.cancelled() and (exc := done.exception()) is not None:
                log.error("task.dispatch_failed", submission=key, error=type(exc).__name__)

        task.add_done_callback(settled)
        accepted.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        await asyncio.shield(accepted)

    async def persist(self, step: UUID, state: TerminalState, result: dict[str, Any]) -> None:
        receipt = Receipt(user_id=current_user.get(), state=state, result=_receipt_value(result))
        data = receipt.model_dump_json().encode()
        if len(data) > MAX_RECEIPT_BYTES:
            metadata = {k: result[k] for k in ("ok", "run_id", "source", "cost") if k in result}
            preview = data[:16_384].decode("utf-8", errors="ignore")
            receipt.result = _receipt_value({
                **metadata, "truncated": True, "bytes": len(data), "preview": preview,
            })
            data = receipt.model_dump_json().encode()
        receipt = Receipt.model_validate_json(data)
        try:
            path = await asyncio.to_thread(completions.write, require_tenant(), step, data, bucket=BUCKET)
        except OSError:
            log.error("task.receipt_spool_unavailable", step=str(step))
            await self.store.record(step, receipt.state, receipt.result)
            return
        async with self.replay_lock:
            try:
                await self._apply(path, receipt)
            except (psycopg.Error, OSError):
                self._ensure_retry()

    def _ensure_retry(self) -> None:
        if self.retry_task is None or self.retry_task.done():
            self.retry_task = asyncio.create_task(self._retry())

    async def _retry(self) -> None:
        while True:
            await asyncio.sleep(5)
            if await self.flush():
                return

    async def flush(self) -> bool:
        async with self.replay_lock:
            try:
                for path in await asyncio.to_thread(completions.entries, bucket=BUCKET):
                    try:
                        receipt = Receipt.model_validate_json(await asyncio.to_thread(completions.read, path))
                    except (ValueError, KeyError, FileNotFoundError):
                        if path.exists():
                            await asyncio.to_thread(completions.quarantine, path, "invalid")
                        continue
                    await self._apply(path, receipt)
                return not await asyncio.to_thread(completions.entries, bucket=BUCKET)
            except (psycopg.Error, OSError):
                log.warning("task.receipt_replay_delayed")
                return False

    async def _apply(self, path: Path, receipt: Receipt) -> None:
        step = UUID(path.stem)
        with use_tenant(UUID(path.parent.name), receipt.user_id):
            stored = await self.store.record(step, receipt.state, receipt.result)
        if stored:
            await asyncio.to_thread(completions.discard, path)
        elif path.exists():
            await asyncio.to_thread(completions.quarantine, path, "conflict")

    async def aclose(self) -> None:
        if self.retry_task is not None:
            self.retry_task.cancel()
            await asyncio.gather(self.retry_task, return_exceptions=True)
        await self.flush()

    async def execute(self, req: ExecRequest) -> ExecResult:
        raise RuntimeError("task dispatch is a lifecycle hook")


async def _run_cli(
    dispatch: TaskDispatch, manager: JobManager, step: UUID, spec: AiJobSpec,
) -> None:
    try:
        async with asyncio.timeout(45):
            await manager.start(spec, caller=current_caller.get(), new_job_id=step)
    except AiRequestError as exc:
        state: TerminalState = "paused" if exc.kind == "account_unavailable" else "failed"
        await dispatch.persist(step, state, {
            "kind": exc.kind, "message": exc.message, "account": exc.account, "choices": exc.choices,
            "retry_at": exc.retry_at,
        })


async def _run_call(
    dispatch: TaskDispatch, step: UUID, call: Callable[[], Awaitable[RouteOutcome]], *, read_only: bool,
) -> None:
    try:
        async with asyncio.timeout(dispatch.ctx.deadline_s + 15):
            outcome = await call()
        error = outcome.error.kind if outcome.error is not None else None
        result = outcome.result or {}
        remote = result.get(RESULT_KEY)
        if error in ("outcome_unknown", "internal"):
            state: TerminalState = "outcome_unknown"
        elif error in ("no_capacity", "limit_reached", "rate_limited", "auth", "needs_login"):
            state = "paused"
        elif not outcome.ok:
            state = "failed"
        elif REJECTION_KEY in result:
            rejection = result[REJECTION_KEY]
            code = rejection.get("code") if isinstance(rejection, dict) else None
            invalid_request = code in (-32600, -32601, -32602)
            state = "failed" if read_only or invalid_request else "outcome_unknown"
        elif isinstance(remote, dict) and remote.get("isError"):
            state = "failed" if read_only else "outcome_unknown"
        else:
            state = "succeeded"
        receipt = outcome.envelope()
    except Exception as exc:
        state = "outcome_unknown"
        receipt = {"run_id": str(step), "error": type(exc).__name__}
    await dispatch.persist(step, state, receipt)


async def register_task_tools(
    server: FastMCP, ctx: FarmContext, manager: JobManager, policy: PolicyCheck | None,
) -> None:
    store = TaskStore(ctx.pool, ctx.clock)
    dispatch = TaskDispatch(ctx, store)
    ctx.executors[f"task-dispatch:{manager.instance_id}"] = dispatch
    if not await dispatch.flush():
        dispatch._ensure_retry()

    @server.tool()
    async def task_open(
        project: str, workspace: str, title: str, goal: str, approach: Approach = "finish",
    ) -> dict[str, Any]:
        """Start a durable task for an absolute workspace path. Choose finish (this IDE does edits) or
        delegate (assign edits or research to workers). Save the task ID and epoch for subsequent calls."""
        return await _view(store.create(_actor(), project, workspace, title, goal, approach))

    @server.tool(annotations={"readOnlyHint": True})
    async def task_list(workspace: str, project: str | None = None) -> dict[str, Any]:
        """Find your unfinished tasks by the same absolute IDE workspace path after switching clients."""
        try:
            return {"tasks": to_jsonable(await store.list(_actor(), workspace, project), None)}
        except TaskError as exc:
            raise ToolError(str(exc)) from None

    @server.tool(annotations={"readOnlyHint": True})
    async def task_status(task_id: UUID, step_key: str | None = None) -> dict[str, Any]:
        """Read the checkpoint, operator history, CLI job IDs and step states. Pass step_key to get its
        saved MCP/API response. Use ai_result with a CLI job ID for the worker's output and recovery data."""
        return await _view(store.get(_actor(), task_id, step_key=step_key))

    @server.tool()
    async def task_takeover(
        task_id: UUID, epoch: int, workspace: str, approach: Approach,
        replace_active: bool = False, workspace_checked: bool = False,
    ) -> dict[str, Any]:
        """Ask the user whether to finish in this IDE or delegate to a worker, then claim the task with
        that choice. Use the epoch from task_status. Confirm replace_active if the previous lease is
        still live. For an interrupted IDE edit, check that the old editor stopped and inspect the
        working tree before setting workspace_checked. Already assigned work keeps running."""
        return await _view(store.take_over(
            _actor(), task_id, epoch, approach, workspace=workspace,
            replace_active=replace_active, workspace_checked=workspace_checked,
        ))

    @server.tool()
    async def task_checkpoint(
        task_id: UUID, epoch: int, note: str | None = None, pause: bool = False,
        finished_local_step: str | None = None,
    ) -> dict[str, Any]:
        """Save file paths, completed work, and remaining actions. A note-free call renews the three-minute
        lease. Pause when stopping, or name a finished local step after saving and checking its files."""
        return await _view(store.checkpoint(
            _actor(), task_id, epoch, note, pause=pause, finished_local_step=finished_local_step,
        ))

    @server.tool()
    async def task_local_edit(task_id: UUID, epoch: int, step_key: str, label: str) -> dict[str, Any]:
        """Before changing files in this IDE, reserve the workspace. Reuse the key and label for an
        interrupted local step. Inspect the files and keep checkpoints as you work."""
        actor = _actor()
        try:
            await store.reserve(actor, task_id, epoch, step_key, "local", label, None, {})
        except TaskError as exc:
            raise ToolError(str(exc)) from None
        return await _view(store.get(actor, task_id, step_key=step_key))

    @server.tool()
    async def task_cli(
        task_id: UUID, epoch: int, step_key: str, label: str, job: AiJobSpec,
    ) -> dict[str, Any]:
        """Assign one tracked CLI job. Reuse the same key and input on a retry to get the same job.
        Give a replacement job a new key and include the task checkpoint and previous output in its
        instructions. Choose model and effort with ai_options. Parallel edit jobs need distinct worktrees."""
        actor = _actor()
        args = job.model_dump(mode="json")
        _allow(policy, "ai_start", args)
        root = _checkout(job.cwd) if job.mode == "edit" else None
        try:
            await dispatch.submit(
                lambda: store.reserve(
                    actor, task_id, epoch, step_key, "cli", label, job.account or job.ai, args,
                    write_root=root, deadline=ctx.clock() + timedelta(seconds=60),
                ),
                lambda step: _run_cli(dispatch, manager, step, job),
                wait_for_start=True,
            )
        except TaskError as exc:
            raise ToolError(str(exc)) from None
        return await _view(store.get(actor, task_id, step_key=step_key))

    @server.tool()
    async def task_call(
        task_id: UUID, epoch: int, step_key: str, label: str, tool: str, arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Start one tracked API capability or upstream MCP tool call. Pass its normal argument object
        and a stable step key. Poll task_status with that key for the saved response. Repeating an
        accepted step returns its state. Verify any unknown outcome with the provider before retrying."""
        from farm.gateway.server import CapabilityTool

        actor = _actor()
        _allow(policy, tool, arguments)
        target = await server.get_tool(tool)
        if not isinstance(target, (CapabilityTool, PassthroughTool)) or tool == "ask_ai":
            raise ToolError("select a Farm API capability or an exposed upstream MCP tool")
        remote = isinstance(target, PassthroughTool)
        try:
            await dispatch.submit(
                lambda: store.reserve(
                    actor, task_id, epoch, step_key, "mcp" if remote else "api", label, tool, arguments,
                    deadline=ctx.clock() + timedelta(seconds=ctx.deadline_s + 30),
                ),
                lambda step: _run_call(
                    dispatch, step, lambda: target.execute_route(arguments, run_id=step),
                    read_only=isinstance(target, PassthroughTool) and target.read_only,
                ),
            )
        except TaskError as exc:
            raise ToolError(str(exc)) from None
        return await _view(store.get(actor, task_id, step_key=step_key))

    @server.tool()
    async def task_resolve_step(
        task_id: UUID, epoch: int, step_key: str, resolution: Resolution, evidence: str,
    ) -> dict[str, Any]:
        """Reconcile a failed or interrupted step after checking its provider or working tree. Use
        completed if you verified the effect, or superseded to make room for a replacement step."""
        return await _view(store.resolve(_actor(), task_id, epoch, step_key, resolution, evidence))

    @server.tool()
    async def task_complete(task_id: UUID, epoch: int, summary: str) -> dict[str, Any]:
        """Finish the task after all assigned work is complete or explicitly reconciled."""
        return await _view(store.finish(_actor(), task_id, epoch, summary))

    @server.tool(annotations={"readOnlyHint": True})
    async def worker_profiles(account: str | None = None) -> dict[str, Any]:
        """Show queued and active jobs, model choices, and observed allowance per account.

        Execution limits are configured ceilings. Provider allowance does not predict remaining jobs.
        """
        _actor()
        return await _worker_profiles(ctx, account)


async def _worker_profiles(ctx: FarmContext, account: str | None) -> dict[str, Any]:
    now = ctx.clock()
    async with tenant_connection(ctx.pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select account,provider,label,status,slots as configured_execution_limit,"
            "busy,queued,running_models,cooldown_until from public.v_worker_activity "
            "where kind='ai' and (%s::text is null or account=%s) order by provider,account",
            (account, account),
        )
        workers = await cur.fetchall()
        await cur.execute(
            "select id,account,model,resolved_model,effort as requested_effort,effective_effort,state,mode,"
            "operator_client "
            "from public.ai_jobs "
            "where state in ('queued','running') and (%s::text is null or account=%s) "
            "order by started_at nulls last,created_at limit 100", (account, account),
        )
        jobs = await cur.fetchall()
    limits = {
        connection.id: connection for pool in await capacity_report(ctx.pool, now)
        for connection in pool.connections
    }
    ai = await list_ai_accounts(ctx.pool, now)
    ai_accounts = {entry["id"]: entry for entry in ai["accounts"]}
    usage = await usage_report(ctx.pool, now)
    allowances = {entry["connection_id"]: entry for entry in usage["provider_allowances"]}
    for row in workers:
        capacity = limits.get(row["account"])
        account_info = ai_accounts.get(row["account"], {})
        row["identity"] = account_info.get("identity")
        row["supported_models"] = account_info.get("models", [])
        row["model_availability"] = account_info.get("model_availability", [])
        row["quota_units"] = capacity.units if capacity else []
        row["provider_allowance"] = allowances.get(row["account"])
        row["remaining_jobs_estimate"] = None
    return cast(dict[str, Any], to_jsonable({
        "observed_at": now, "configured_global_execution_limit": ctx.max_parallel, "workers": workers,
        "jobs": jobs, "ai_pools": ai["ais"],
    }, None))
