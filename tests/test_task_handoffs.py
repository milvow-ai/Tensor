from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import mcp_types
import psycopg
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from farm.ai import completions
from farm.control.run import create_farm_app, create_token
from farm.control.task_handoffs import TaskStore
from farm.db.pool import DbPool
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.gateway.task_tools import BUCKET
from farm.registry import Registry
from farm.tenancy.members import add_member
from farm.tenancy.models import Role
from tests.farm_helpers import EMAIL, ok_result
from tests.mcp_helpers import mcp_provider, mcp_registry, world_factory
from tests.test_ai_jobs import Aip, aip_factory, call, claude_limit, claude_ok, eventually, finish

__all__ = ["aip_factory"]

WORKSPACE = "/projects/fosa"


async def open_task(client: Client[Any], approach: str = "delegate", workspace: str = WORKSPACE) -> str:
    opened = await call(
        client, "task_open", project="fosa", workspace=workspace,
        title="Deliver the change", goal="Update the project and check the integration", approach=approach,
    )
    return str(opened["id"])


async def get_step(client: Client[Any], task: str, step: str) -> dict[str, Any]:
    return dict((await call(client, "task_status", task_id=task, step_key=step))["steps"][0])


async def tracked_call(client: Client[Any], **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool("task_call", arguments)
    assert result.structured_content is not None
    return dict(result.structured_content)


async def test_two_clients_preserve_tracked_api_and_mcp_results_on_takeover(
    pool: DbPool, registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("farm.control.cli._mcp_exposure", lambda: (40, []))
    mcp_started, api_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    received: list[str] = []
    upstream = FastMCP("upstream")

    @upstream.tool()
    async def update_issue(title: str) -> str:
        received.append(title)
        mcp_started.set()
        await release.wait()
        return f"Saved {title}"

    class DelayedApi:
        async def execute(self, request: ExecRequest) -> ExecResult:
            received.append(request.capability)
            api_started.set()
            await release.wait()
            return ok_result()

    registry.providers.update(mcp_registry(jira=mcp_provider()).providers)
    alice = await add_member(pool, "alice@example.com", Role.MEMBER, str(uuid4()))
    bob = await add_member(pool, "bob@example.com", Role.MEMBER, str(uuid4()))
    claude_token = await create_token(pool, "claude-1", user_id=alice, scopes=("mcp",))
    codex_token = await create_token(pool, "codex-1", user_id=alice, scopes=("mcp",))
    bob_token = await create_token(pool, "bob-ide", user_id=bob, scopes=("mcp",))
    limited_token = await create_token(pool, "limited", user_id=alice, scopes=("tool:task_call",))

    async with world_factory(pool, tmp_path) as make:
        world = await make(registry, {"jira-01": upstream})
        world.ctx.executors["api"] = DelayedApi()
        await world.sync()
        app = await create_farm_app(world.ctx)

        def http_client(**options: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app), **options)

        def connect(token: str, name: str) -> Client[Any]:
            return Client(
                StreamableHttpTransport(
                    "http://127.0.0.1/mcp", headers={"Authorization": f"Bearer {token}"},
                    httpx_client_factory=http_client,
                ),
                client_info=mcp_types.Implementation(name=name, version="1"),
            )

        base = app.app
        async with base.router.lifespan_context(base):
            try:
                async with connect(claude_token, "Claude Code") as claude:
                    task = await open_task(claude)
                    calls = {
                        "jira": {"tool": "jira__update_issue", "arguments": {"title": "FOSA"}},
                        "verify": {"tool": "verify_email", "arguments": {"email": EMAIL}},
                    }
                    for key, args in calls.items():
                        params = {"task_id": task, "epoch": 1, "step_key": key, "label": key, **args}
                        first, duplicate = await asyncio.gather(
                            tracked_call(claude, **params), tracked_call(claude, **params),
                        )
                        assert first["steps"][0]["id"] == duplicate["steps"][0]["id"]
                    await asyncio.wait_for(asyncio.gather(api_started.wait(), mcp_started.wait()), 5)
                    await call(claude, "task_checkpoint", task_id=task, epoch=1, note="Two calls are in flight", pause=True)

                async with connect(bob_token, "Cursor") as other:
                    with pytest.raises(ToolError, match="task not found"):
                        await other.call_tool("task_status", {"task_id": task})
                async with connect(limited_token, "Cursor") as limited:
                    with pytest.raises(ToolError, match="scope"):
                        await limited.call_tool(
                            "task_call", {"task_id": task, "epoch": 1, "step_key": "jira", "label": "jira", **calls["jira"]},
                        )

                async with connect(codex_token, "Codex") as codex:
                    assert (await call(codex, "task_list", workspace=WORKSPACE))["tasks"][0]["id"] == task
                    claimed = await call(codex, "task_takeover", task_id=task, epoch=1, workspace=WORKSPACE, approach="finish")
                    assert claimed["epoch"] == 2 and claimed["is_operator"]
                    assert claimed["operator_history"][-1]["previous_actor"].startswith("claude-1")
                    assert claimed["operator_client"].startswith("codex-1")
                    for key, args in calls.items():
                        retried = await tracked_call(codex, task_id=task, epoch=2, step_key=key, label=key, **args)
                        assert retried["steps"][0]["state"] == "running"
                    release.set()

                    async def receipts_saved() -> bool:
                        return all(
                            step["state"] == "succeeded"
                            for step in (await call(codex, "task_status", task_id=task))["steps"]
                        )

                    await eventually(receipts_saved)
                    mcp_step = await get_step(codex, task, "jira")
                    api_step = await get_step(codex, task, "verify")
                    assert mcp_step["receipt"]["result"]["call_tool_result"]["content"][0]["text"] == "Saved FOSA"
                    assert api_step["receipt"]["result"] == ok_result().data
                    assert mcp_step["receipt"]["run_id"] == mcp_step["id"] == mcp_step["run_id"]
                    await call(codex, "task_complete", task_id=task, epoch=2, summary="Both calls completed")
            finally:
                release.set()
    assert sorted(received) == ["FOSA", "verify_email"]


async def test_cli_worker_keeps_jobs_while_new_operator_chooses_how_to_finish(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    farm = await aip_factory(claude_parallel=2)
    farm.harness.set_response(stdout=claude_ok("worker result"), delay_s=3)
    async with farm.client("Claude Code") as claude, farm.client("Codex") as codex:
        task = await open_task(claude)
        ids = []
        for key, model in [("a", "sonnet"), ("b", "opus"), ("c", "sonnet")]:
            submitted = await call(
                claude, "task_cli", task_id=task, epoch=1, step_key=key, label=key,
                job={"task": f"Analyze {key}", "account": "claude-02", "ai": "claude", "model": model, "effort": "high"},
            )
            ids.append(submitted["steps"][0]["job_id"])

        async def slots_full() -> bool:
            profile = await call(codex, "worker_profiles", account="claude-02")
            worker = profile["workers"][0]
            if worker["busy"] != 2 or worker["queued"] != 1:
                return False
            assert worker["configured_execution_limit"] == 2 and worker["quota_units"]
            assert worker["remaining_jobs_estimate"] is None and "open_slots" not in worker
            assert worker["running_models"] == [{"model": "opus", "jobs": 1}, {"model": "sonnet", "jobs": 1}]
            assert {job["id"] for job in profile["jobs"]} == set(ids)
            return True

        await eventually(slots_full)
        options = {"task_id": task, "epoch": 1, "workspace": WORKSPACE, "approach": "finish"}
        with pytest.raises(ToolError, match="still holds the lease"):
            await codex.call_tool("task_takeover", options)
        await call(codex, "task_takeover", **options, replace_active=True)
        with pytest.raises(ToolError, match="another operator"):
            await claude.call_tool("task_checkpoint", {"task_id": task, "epoch": 1, "note": "old operator"})
        with pytest.raises(ToolError, match="choose delegate"):
            await codex.call_tool("task_cli", {
                "task_id": task, "epoch": 2, "step_key": "later", "label": "Later", "job": {"task": "New work"},
            })
        finished = await finish(codex, ids)
        assert all(job["state"] == "succeeded" for job in finished)
        assert len(farm.harness.events()) == 3
        await call(codex, "task_local_edit", task_id=task, epoch=2, step_key="edit", label="Apply findings")
        await call(
            codex, "task_checkpoint", task_id=task, epoch=2,
            finished_local_step="edit", note="Applied and reviewed the worker findings",
        )
        assert (await call(codex, "task_complete", task_id=task, epoch=2, summary="Delivered"))["state"] == "done"


async def test_interrupted_local_edit_requires_inspection_and_holds_checkout(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    farm = await aip_factory()
    async with farm.client("Claude Code") as claude, farm.client("Codex") as codex:
        task = await open_task(claude, "finish", "D:/Work/Fosa")
        await call(claude, "task_local_edit", task_id=task, epoch=1, step_key="edit", label="Apply changes")
        await call(claude, "task_checkpoint", task_id=task, epoch=1, pause=True, note="Edited app.py")
        takeover = {"task_id": task, "epoch": 1, "workspace": "d:\\work\\fosa", "approach": "finish"}
        with pytest.raises(ToolError, match="inspect the workspace"):
            await codex.call_tool("task_takeover", takeover)
        await call(codex, "task_takeover", **takeover, workspace_checked=True)
        other = await open_task(codex, "finish", "D:/work/fosa/subdir")
        with pytest.raises(ToolError, match="another edit owns this workspace"):
            await codex.call_tool("task_local_edit", {"task_id": other, "epoch": 1, "step_key": "edit", "label": "Edit"})
        resumed = await call(codex, "task_local_edit", task_id=task, epoch=2, step_key="edit", label="Apply changes")
        assert resumed["steps"][0]["state"] == "running"
        assert resumed["checkpoint"] == "Edited app.py"


async def test_unknown_api_outcome_survives_database_error_without_replaying_call(
    aip_factory: Callable[..., Awaitable[Aip]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    farm = await aip_factory()
    sent = []

    class AmbiguousApi:
        async def execute(self, request: ExecRequest) -> ExecResult:
            sent.append(request.connection.id)
            return ExecResult(ok=False, error_kind=ErrorKind.TIMEOUT, request_sent=True)

    farm.ctx.executors["api"] = AmbiguousApi()
    real_record = TaskStore.record

    async def unavailable(self: TaskStore, *args: Any) -> None:
        raise psycopg.OperationalError("simulated temporary database outage")

    monkeypatch.setattr(TaskStore, "record", unavailable)
    async with farm.client() as client:
        task = await open_task(client)
        args = {"task_id": task, "epoch": 1, "step_key": "send", "label": "Send",
                "tool": "verify_email", "arguments": {"email": EMAIL}}
        await tracked_call(client, **args)
        await eventually(lambda: len(completions.entries(bucket=BUCKET)) == 1)
        monkeypatch.setattr(TaskStore, "record", real_record)

        async def receipt_replayed() -> bool:
            return (await get_step(client, task, "send"))["state"] == "outcome_unknown"

        await eventually(receipt_replayed)
        repeated = await tracked_call(client, **args)
        assert repeated["steps"][0]["receipt"]["error"]["kind"] == "outcome_unknown"
        assert len(sent) == 1
        with pytest.raises(ToolError, match="reconcile"):
            await client.call_tool("task_complete", {"task_id": task, "epoch": 1, "summary": "Too early"})
        await call(
            client, "task_resolve_step", task_id=task, epoch=1, step_key="send", resolution="completed",
            evidence="Provider confirmed the request was applied",
        )
        await call(client, "task_complete", task_id=task, epoch=1, summary="Verified")


async def test_quota_stopped_cli_step_can_be_delegated_by_new_operator(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    farm = await aip_factory()
    farm.harness.set_response(stdout=claude_limit(), exit_code=1)
    async with farm.client("Claude Code") as claude, farm.client("Codex") as codex:
        task = await open_task(claude)
        job = {"task": "Check the branch", "ai": "claude", "account": "claude-02", "model": "sonnet", "effort": "high"}
        await call(claude, "task_cli", task_id=task, epoch=1, step_key="original", label="Check branch", job=job)

        async def paused_for_quota() -> bool:
            return (await get_step(codex, task, "original"))["state"] == "paused"

        await eventually(paused_for_quota)
        original = await get_step(codex, task, "original")
        assert original["error_kind"] == "limit" and original["retry_at"]
        await call(claude, "task_checkpoint", task_id=task, epoch=1, pause=True, note="Branch ready for review")
        await call(codex, "task_takeover", task_id=task, epoch=1, workspace=WORKSPACE, approach="delegate")
        await call(
            codex, "task_resolve_step", task_id=task, epoch=2, step_key="original",
            resolution="superseded", evidence="The original step was read-only and stopped at quota",
        )
        farm.harness.set_response(stdout=claude_ok("Review finished"))
        replacement = await call(
            codex, "task_cli", task_id=task, epoch=2, step_key="replacement", label="Finish review",
            job={**job, "account": "claude-03", "task": "The branch is ready for review. Finish the check."},
        )
        new_id = replacement["steps"][0]["job_id"]
        assert new_id and new_id != original["job_id"]
        assert (await finish(codex, [new_id]))[0]["state"] == "succeeded"
        completed = await call(codex, "task_complete", task_id=task, epoch=2, summary="Review done")
        assert {step["state"] for step in completed["steps"]} == {"succeeded", "superseded"}
