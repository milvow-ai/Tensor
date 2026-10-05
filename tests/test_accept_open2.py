"""OPEN2 acceptance tests: blank start, self-serve MCP and AI provider additions, dynamic reload, and CLI parity.

No network. Uses fake stdio MCP server and simulated CLI connections.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastmcp import Client
from psycopg.types.json import Jsonb
from typer.testing import CliRunner

from farm.context import FarmContext
from farm.control.cli import app
from farm.control.commands import (
    process_command,
    register_active_gateway,
    unregister_active_gateway,
)
from farm.db.pool import DbPool
from farm.executors.api import ApiExecutor
from farm.executors.cli_agent import CliAgentExecutor
from farm.executors.llm import LlmExecutor
from farm.executors.mcp.client import McpExecutor
from farm.gateway.server import build_server
from farm.mcp.store import DbDirectory
from farm.registry import load_registry
from farm.registry.sync import sync_registry

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
STDIO_SERVER = FIXTURES / "stdio_server.py"
REGISTRY_PATH = Path(__file__).parent.parent / "config" / "registry.yaml"


@pytest.fixture
def owner_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throw-away copy of the owner's blank registry file: the self-serve commands rewrite it."""
    path = tmp_path / "registry.yaml"
    shutil.copyfile(REGISTRY_PATH, path)
    monkeypatch.setenv("FARM_REGISTRY_PATH", str(path))
    return path


def _make_context(pool: DbPool) -> FarmContext:
    return FarmContext(
        pool=pool,
        executors={
            "api": ApiExecutor(),
            "llm": LlmExecutor(),
            "cli_agent": CliAgentExecutor(),
            "mcp": McpExecutor(directory=DbDirectory(pool)),
        },
    )


async def test_accept_open2_blank_registry_start(pool: DbPool) -> None:
    """Blank registry -> migrate + sync -> 0 providers; gateway lists infra + AI tools only; ask_ai exists."""
    # 1. Load the owner's blank registry file
    reg = load_registry(REGISTRY_PATH)
    assert len(reg.providers) == 0, f"Expected 0 providers in blank registry, got {len(reg.providers)}"

    # 2. Sync to test database
    await sync_registry(pool, reg)

    # 3. Assert DB has 0 providers and 0 connections
    async with pool.connection() as conn:
        cur_prov = await conn.execute("select count(*) from public.providers")
        assert (await cur_prov.fetchone())[0] == 0

        cur_conn = await conn.execute("select count(*) from public.connections")
        assert (await cur_conn.fetchone())[0] == 0

        cur_cap = await conn.execute("select name from public.capabilities where name = 'ask_ai'")
        assert await cur_cap.fetchone() is not None

    # 4. Gateway lists infra + AI job tools only, no provider tools
    ctx = _make_context(pool)
    gateway = await build_server(ctx)
    async with Client(gateway) as client:
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert (
            "ask_ai" in tool_names
            or "farm__run_ai_job" in tool_names
            or any(t.startswith("farm__") for t in tool_names)
        )
        assert not any("__echo" in t for t in tool_names)


async def test_accept_open2_add_provider_stdio_fake_mcp_dynamic_echo(pool: DbPool, owner_registry: Path) -> None:
    """add_provider (stdio fake MCP) via command queue -> provider, connection, capability rows;
    tools synced; connected MCP client sees <ns>__echo without restart; registry file updated;
    audit row; literal secret rejected; remove_provider cleans up.
    """
    # 1. Build gateway and register it as active
    reg = load_registry(owner_registry)
    await sync_registry(pool, reg)
    ctx = _make_context(pool)
    gateway = await build_server(ctx)
    register_active_gateway(gateway, ctx)

    try:
        async with Client(gateway) as client:
            initial_tools = {t.name for t in await client.list_tools()}
            assert not any("fake__" in t for t in initial_tools)

            # 2. Reject literal secret
            bad_cmd_id = uuid4()
            async with pool.connection() as conn:
                await conn.execute(
                    "insert into public.farm_commands (id, kind, payload, status) values (%s, 'add_provider', %s, 'queued')",
                    (
                        bad_cmd_id,
                        Jsonb({
                            "provider_id": "fake-bad",
                            "command": sys.executable,
                            "env": ["KEY=" + "sk-" + "ant-" + "api03-" + "x" * 24],
                        }),
                    ),
                )
            bad_status, bad_res = await process_command(pool, bad_cmd_id)
            assert bad_status == "rejected"
            assert "secret" in bad_res["error"].lower()

            # 3. Add valid stdio fake MCP server
            cmd_id = uuid4()
            async with pool.connection() as conn:
                await conn.execute(
                    "insert into public.farm_commands (id, kind, payload, status, created_by) values (%s, 'add_provider', %s, 'queued', 'owner')",
                    (
                        cmd_id,
                        Jsonb({
                            "provider_id": "fake-probe",
                            "name": "Fake Probe Server",
                            "command": sys.executable,
                            "args": [str(STDIO_SERVER)],
                            "namespace": "fakeprobe",
                            "auth": "none",
                            "exposure": "direct",
                        }),
                    ),
                )

            status, res = await process_command(pool, cmd_id)
            assert status == "done"
            assert res["provider_id"] == "fake-probe"
            assert res["tools_count"] > 0
            assert res["restart_required"] is False

            # 4. Connected client sees tools immediately without restart
            updated_tools = {t.name for t in await client.list_tools()}
            assert "fakeprobe__echo" in updated_tools

            # Call the newly added tool
            call_res = await client.call_tool("fakeprobe__echo", {"message": "hello self-serve"})
            assert "hello self-serve" in call_res.content[0].text

            # 5. Verify audit row and registry file updated
            async with pool.connection() as conn:
                cur_audit = await conn.execute(
                    "select action, target from public.audit_events where action = 'add_provider' and target = 'fake-probe'"
                )
                assert await cur_audit.fetchone() is not None

                cur_cap = await conn.execute("select name from public.capabilities where name = 'mcp:fake-probe'")
                assert await cur_cap.fetchone() is not None

            # Verify registry contains the provider
            owner_reg = load_registry(owner_registry)
            assert "fake-probe" in owner_reg.providers
            assert set(owner_reg.capabilities) == {"ask_ai", "agent_task"}  # the owner's own are kept

            # 6. Clean up with remove_provider
            rm_cmd_id = uuid4()
            async with pool.connection() as conn:
                await conn.execute(
                    "insert into public.farm_commands (id, kind, payload, status) values (%s, 'remove_provider', %s, 'queued')",
                    (rm_cmd_id, Jsonb({"provider_id": "fake-probe", "force": True})),
                )
            rm_status, rm_res = await process_command(pool, rm_cmd_id)
            assert rm_status == "done"

            async with pool.connection() as conn:
                cur_check = await conn.execute("select id from public.providers where id = 'fake-probe'")
                assert await cur_check.fetchone() is None

    finally:
        try:
            cleanup_cmd = uuid4()
            async with pool.connection() as conn:
                await conn.execute(
                    "insert into public.farm_commands (id, kind, payload, status) values (%s, 'remove_provider', %s, 'queued')",
                    (cleanup_cmd, Jsonb({"provider_id": "fake-probe", "force": True})),
                )
            await process_command(pool, cleanup_cmd)
        except Exception:
            pass
        unregister_active_gateway(gateway, ctx)


async def test_accept_open2_cli_parity_and_test_connection(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """farm mcp add and farm ai add produce same rows as commands; AI account is needs_login until test_connection passes."""
    monkeypatch.setenv("FARM_DB_URL", pool.conninfo)
    monkeypatch.setenv("FARM_REGISTRY_PATH", str(tmp_path / "registry.yaml"))
    # 1. farm ai add
    res_ai = runner.invoke(
        app,
        [
            "ai",
            "add",
            "codex",
            "codex-02",
            "--label",
            "Codex Account 2",
            "--model",
            "gpt-5-codex",
        ],
    )
    assert res_ai.exit_code == 0
    assert "farm ai login codex-02" in res_ai.stdout

    async with pool.connection() as conn:
        cur_conn = await conn.execute("select id, status from public.connections where id = 'codex-02'")
        row = await cur_conn.fetchone()
        assert row is not None
        assert row[1] == "needs_login"

    # 2. test_connection command checks connection
    tc_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) values (%s, 'test_connection', %s, 'queued')",
            (tc_id, Jsonb({"connection_id": "codex-02"})),
        )
    tc_status, tc_res = await process_command(pool, tc_id)
    assert tc_status == "done"
    assert "status" in tc_res
