"""R2 recovery regressions: a failed attempt's partial output, and recovery approval over real HTTP tokens."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import tests.test_ai_jobs as ai_jobs
from farm.ai.jobs import JobManager
from farm.control.run import create_farm_app, create_token
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.gateway.ai_tools import ManagerHook
from farm.resources.router import MAX_PARTIAL_CHARS
from tests.farm_helpers import fetch
from tests.test_ai_jobs import (
    WAIT_S,
    Aip,
    ScriptedTransport,
    answered,
    failed_recovery_parent,
    recovery_context,
    scripted_manager,
    spec,
)

# The AI-pool fixtures, exposed here by name (assigned, not imported, so test arguments do not redefine them).
aip = ai_jobs.aip
aip_factory = ai_jobs.aip_factory

PARTIAL = "half of the answer, then the worker died"


class PartialThenFails:
    """A ``cli_agent`` executor whose call fails after the worker printed part of an answer."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def execute(self, req: ExecRequest) -> ExecResult:
        self.calls += 1
        return ExecResult(
            ok=False, error_kind=ErrorKind.TIMEOUT, error="worker timed out", data={"text": self.text}
        )


@pytest.mark.parametrize(
    "text, truncated",
    [(PARTIAL, False), ("y" * MAX_PARTIAL_CHARS, False), ("z" * (MAX_PARTIAL_CHARS + 10_000), True)],
    ids=["short", "exactly-at-limit", "over-limit"],
)
async def test_partial_output_of_a_failed_attempt_reaches_recovery_but_is_never_an_answer(
    aip: Aip, text: str, truncated: bool
) -> None:
    executor = PartialThenFails(text)
    aip.ctx.executors["cli_agent"] = executor
    manager = JobManager(aip.ctx, tick_s=0.05)  # the real RouterTransport
    aip.ctx.executors["ai-jobs:partial"] = ManagerHook(manager)  # stopped with the context
    started = await manager.start(
        spec(task="write the report", ai="claude", account="claude-02"), caller="test"
    )
    (result,), _ = await manager.wait([started.job_id], mode="all", timeout_s=WAIT_S)
    kept = text[:MAX_PARTIAL_CHARS]

    # router -> Attempt -> stored failed result: kept for inspection, never reported as success
    assert executor.calls == 1
    assert not result.ok and result.state == "failed" and result.error is not None
    assert result.error.kind == "timeout"
    assert result.partial_output is True and result.result_chars == len(kept)
    if truncated:
        assert result.text_truncated is True and result.note is not None
    if text == PARTIAL:
        assert result.text == PARTIAL
    record = await manager.store.get(started.job_id)
    assert record is not None and record.state == "failed" and record.result_path is not None
    assert record.attempts[-1]["partial_chars"] == len(text)  # the full length survives the clipping
    runs = await fetch(aip.pool, "select status, cached from public.runs where id = %s", record.run_id)
    assert runs == [("failed", False)]
    # never cached: no capability request may hold the partial text as a result
    cached = await fetch(
        aip.pool,
        "select count(*) from public.capability_requests where result::text like %s",
        f"%{text[:64]}%",
    )
    assert cached == [(0,)]

    # stored failed result -> recovery context, flagged incomplete
    replay = ScriptedTransport(lambda job, task: answered(job.account, "done"))
    recovering = scripted_manager(aip, replay)
    child = await recovering.recover_job(started.job_id, caller="test")
    (recovered,), _ = await recovering.wait([child.job_id], mode="all", timeout_s=WAIT_S)
    assert recovered.ok
    job, task = replay.calls[0]
    assert job.account != "claude-02"
    context = recovery_context(task)
    assert context["partial_output"] == kept and context["partial_output_available"] is True
    assert context["partial_output_truncated"] is truncated
    assert "must not be treated as final" in task.partition("\n")[0]


def _http_client(app: Any, token: str) -> Client[Any]:
    def factory(headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            headers=headers,
            timeout=kwargs.get("timeout"),
            auth=kwargs.get("auth"),
        )

    return Client(
        StreamableHttpTransport(
            "http://127.0.0.1/mcp", headers={"Authorization": f"Bearer {token}"}, httpx_client_factory=factory
        )
    )


async def _recover(client: Client[Any], **arguments: Any) -> dict[str, Any]:
    reply = await client.call_tool("ai_recover", arguments, raise_on_error=False)
    assert reply.structured_content is not None, reply.content
    return dict(reply.structured_content)


async def test_over_http_a_worker_token_cannot_approve_recovery_and_an_operator_token_can(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(codex=1)
    answer_parent = await failed_recovery_parent(aip)
    edit_parent = await failed_recovery_parent(aip, task="durable edit task", mode="edit")
    worker = await create_token(aip.pool, "worker-bot", scopes=("mcp",))
    operator = await create_token(aip.pool, "ops-lead", scopes=("ai:recovery:approve",))
    ((operator_id,),) = await fetch(aip.pool, "select id from public.api_tokens where name = %s", "ops-lead")
    app = await create_farm_app(aip.ctx)  # production stack: TokenTrajectoryMiddleware only

    base = getattr(app, "app", app)
    async with base.router.lifespan_context(base):
        async with _http_client(app, worker) as client:
            for arguments in (
                {"job_id": str(answer_parent.id), "ai": "codex", "approve_provider_change": True},
                {"job_id": str(answer_parent.id), "model": "opus", "approve_model_change": True},
                {"job_id": str(edit_parent.id), "acknowledge_reconciliation": True},
            ):
                refused = await _recover(client, **arguments)
                assert refused["ok"] is False and refused["error"]["kind"] == "approval_required", arguments
        assert await fetch(
            aip.pool, "select count(*) from public.ai_jobs where recovery_parent_id = any(%s)",
            [answer_parent.id, edit_parent.id],
        ) == [(0,)]

        async with _http_client(app, operator) as client:
            moved = await _recover(
                client, job_id=str(answer_parent.id), ai="codex", model="gpt-5", effort="high", approve_provider_change=True, approve_model_change=True
            )
            edited = await _recover(client, job_id=str(edit_parent.id), acknowledge_reconciliation=True)
        assert moved["ok"] is True and edited["ok"] is True, (moved, edited)

    actor = f"token:{operator_id}:ops-lead"
    for started, parent in ((moved, answer_parent), (edited, edit_parent)):
        child = await aip.manager.store.get(UUID(started["job_id"]))
        assert child is not None and child.recovery_parent_id == parent.id
        assert child.recovery_approval_actor == actor
        assert child.caller == "ops-lead"
    assert (await aip.manager.store.get(UUID(moved["job_id"]))).ai == "codex"  # type: ignore[union-attr]
