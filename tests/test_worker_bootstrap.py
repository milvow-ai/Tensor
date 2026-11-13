from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from farm.ai.jobs import JobManager
from farm.ai.worker_bootstrap import TOKEN_ENV, WorkerBootstrap, WorkerLaunch, current_worker_launch
from farm.control.run import create_farm_app, create_token, revoke_token
from farm.db.pool import DbPool
from farm.executors.base import ConnectionView, ExecRequest, ExecResult
from farm.executors.cli_agent import base, gemini_cli
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from farm.executors.cli_agent.codex import CodexCliExecutor
from farm.registry import Registry
from farm.tenancy import Role
from farm.tenancy.members import add_member
from tests.fake_mcp_server import create_fake_rich_server
from tests.mcp_helpers import mcp_provider, mcp_registry, world_factory

URL = "http://127.0.0.1:8765/mcp"
FAKE_BEARER = "hf_job_" + "B" * 43


class HeldCli:
    def __init__(self) -> None:
        self.started: asyncio.Queue[WorkerLaunch] = asyncio.Queue()
        self.release: asyncio.Queue[None] = asyncio.Queue()

    async def execute(self, request: ExecRequest) -> ExecResult:
        worker = current_worker_launch.get()
        assert worker is not None and worker.bearer not in request.model_dump_json()
        await self.started.put(worker)
        await self.release.get()
        return base.BaseCliAgentExecutor.build_exec_result(ok=True, ai="claude", connection=request.connection)


async def test_queued_workers_receive_only_their_operators_current_grants(
    pool: DbPool, registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FARM_WORKER_MCP_URL", URL)
    monkeypatch.setenv("FARM_WORKER_MCP_TOOLS", "fake__echo")
    alice = await add_member(pool, "alice@example.com", Role.MEMBER, "fixture-alice")
    bob = await add_member(pool, "bob@example.com", Role.MEMBER, "fixture-bob")
    token_a = await create_token(pool, "alice-ide", user_id=alice, scopes=("mcp",))
    token_b = await create_token(pool, "bob-ide", user_id=bob, scopes=(
        "tool:ai_start", "tool:farm_operator", "tool:search_tools", "tool:call_tool",
    ))
    registry.providers.update(mcp_registry(fake=mcp_provider()).providers)
    async with world_factory(pool, tmp_path) as make:
        world = await make(registry, {"fake-01": create_fake_rich_server()})
        await world.sync()
        held = HeldCli()
        world.ctx.executors["cli_agent"] = held
        world.ctx.max_parallel = 1
        world.ctx._execution_gate = asyncio.Semaphore(1)
        manager = JobManager(world.ctx)
        app = await create_farm_app(world.ctx, job_manager=manager, port=8765)
        assert manager.worker_bootstrap is not None

        def factory(headers: dict[str, str] | None = None, **options: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app), headers=headers, **options)

        def client_for(bearer: str) -> Client[Any]:
            return Client(StreamableHttpTransport(
                URL, headers={"Authorization": f"Bearer {bearer}"}, httpx_client_factory=factory,
            ))

        async def start(client: Client[Any]) -> UUID:
            result = await client.call_tool("ai_start", {
                "ai": "claude", "model": "sonnet", "effort": "medium", "task": "fixture",
            })
            assert result.structured_content is not None
            return UUID(result.structured_content["job_id"])

        server = getattr(app, "app", app)
        async with server.router.lifespan_context(server):
            async with client_for(token_a) as client:
                job_a = await start(client)
            worker_a = await asyncio.wait_for(held.started.get(), timeout=5)
            async with client_for(token_b) as client:
                job_b = await start(client)
            assert held.started.empty()
            assert world.ctx.execution_gate.locked()
            async with client_for(worker_a.bearer) as client:
                tools = {tool.name for tool in await client.list_tools()}
                assert {"fake__echo", "call_tool", "farm_operator"} <= tools
                assert not {"ai_start", "ai_recover", "task_cli", "verify_email"} & tools
                identity = (await client.call_tool("farm_operator", {})).structured_content
                assert identity and identity["user_id"] == str(alice)
                for name, arguments in (
                    ("fake__echo", {"message": "direct"}),
                    ("call_tool", {"name": "fake__echo", "arguments": {"message": "proxy"}}),
                ):
                    result = await asyncio.wait_for(client.call_tool(name, arguments), timeout=5)
                    assert result.content[0].text.startswith("echo:")  # type: ignore[union-attr]
                with pytest.raises(ToolError, match="scope"):
                    await client.call_tool("ai_cancel", {"job_id": str(job_a)})
                with pytest.raises(ToolError, match="Unknown tool"):
                    await client.call_tool("call_tool", {"name": "ai_start"})
                with pytest.raises(ToolError, match="credentials cannot be used"):
                    await client.call_tool("fake__echo", {"message": worker_a.bearer})
            await held.release.put(None)
            worker_b = await asyncio.wait_for(held.started.get(), timeout=5)
            assert await manager.worker_bootstrap.verify(worker_a.bearer) is None
            assert worker_b.bearer != worker_a.bearer
            async with client_for(worker_b.bearer) as client:
                identity = (await client.call_tool("farm_operator", {})).structured_content
                assert identity and identity["user_id"] == str(bob)
                assert "fake__echo" not in {tool.name for tool in await client.list_tools()}
                with pytest.raises(ToolError, match="scope"):
                    await client.call_tool("fake__echo", {"message": "denied"})
                with pytest.raises(ToolError, match="Unknown tool"):
                    await client.call_tool("call_tool", {"name": "fake__echo"})
            assert await revoke_token(pool, "bob-ide")
            assert await manager.worker_bootstrap.verify(worker_b.bearer) is None
            await manager.cancel(job_b)
            async with pool.connection() as conn:
                rows = await (await conn.execute(
                    "select operator_user_id, state from ai_jobs order by created_at"
                )).fetchall()
                assert rows == [(alice, "succeeded"), (bob, "cancelled")]


@pytest.mark.parametrize("driver", ["claude", "codex", "gemini"])
async def test_native_launch_uses_reference_and_sanitizes_echoed_bearer(
    driver: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    launches: list[tuple[list[str], dict[str, str], bytes | None]] = []
    claude_files: list[Path] = []

    def fake_process(
        _state: Any, _name: str, argv: list[str], _cwd: str | None, env: dict[str, str],
        _timeout: float, _max_bytes: int, input_bytes: bytes | None,
    ) -> base.SubprocessOutput:
        launches.append((argv, env, input_bytes))
        if driver == "claude":
            path = Path(argv[argv.index("--mcp-config") + 1])
            claude_files.append(path)
            assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["harness-farm"] == {
                "type": "http", "url": URL, "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
            }
        return base.SubprocessOutput(0, json.dumps({"result": FAKE_BEARER}), FAKE_BEARER, 0.1)

    monkeypatch.setattr(base, "_run_sync_worker", fake_process)
    monkeypatch.setattr(base.shutil, "which", lambda _: sys.executable)
    monkeypatch.setattr(gemini_cli, "_trusted_worker_settings", lambda path, url: url == URL)
    monkeypatch.setenv("FARM_WORKER_GEMINI_SETTINGS", str(tmp_path / "managed.json"))
    conn = ConnectionView(id=f"{driver}-01", provider_id=driver, auth_ref="cli:fixture", meta={
        "home": str(tmp_path), "config_dir": str(tmp_path),
        "env": {TOKEN_ENV: "spoofed", "OTHER_SECRET": "blocked", "SAFE": "present"},
    })
    cls = {"claude": ClaudeCliExecutor, "codex": CodexCliExecutor, "gemini": gemini_cli.GeminiCliExecutor}[driver]
    token = current_worker_launch.set(WorkerLaunch(URL, ("fake__echo",), FAKE_BEARER))
    try:
        request = ExecRequest(request_id=UUID(int=1), capability="ask_ai", params={"task": "fixture"},
                              connection=conn)
        result = await cls().execute(request)
    finally:
        current_worker_launch.reset(token)
    assert result.ok and result.data and result.data["text"] == "***"
    [(argv, env, stdin)] = launches
    assert FAKE_BEARER not in json.dumps(argv) and FAKE_BEARER.encode() not in (stdin or b"")
    assert env[TOKEN_ENV] == FAKE_BEARER and "OTHER_SECRET" not in env and env["SAFE"] == "present"
    if driver == "codex":
        assert any(URL in value and f'bearer_token_env_var="{TOKEN_ENV}"' in value for value in argv)
    if driver == "gemini":
        assert env["GEMINI_CLI_HOME"] == str(tmp_path)
        assert env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == str(tmp_path / "managed.json")
        assert argv[argv.index("--allowed-mcp-server-names") + 1] == "harness-farm"
    assert all(not path.exists() for path in claude_files)


@pytest.mark.parametrize("url", ["https://example.com/mcp", "http://127.0.0.1:8766/mcp", URL + "?token=bad"])
def test_worker_endpoint_is_explicitly_local(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_WORKER_MCP_URL", url)
    with pytest.raises(ValueError, match="literal loopback"):
        WorkerBootstrap.configured(None, "/mcp", 1, port=8765)  # type: ignore[arg-type]
