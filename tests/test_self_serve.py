"""Tests for OPEN2 self-serve integrations: CLI parity, secret rejection, next steps, and remove guards."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from typer.testing import CliRunner

from farm.control.cli import app
from farm.control.commands import process_command
from farm.db.pool import DbPool

runner = CliRunner()


async def test_add_provider_rejects_raw_secrets(pool: DbPool) -> None:
    """Literal secrets anywhere in provider spec, env, or headers are rejected."""
    # 1. Raw secret in MCP env
    cmd1_id = uuid4()
    secret_value = "sk-ant-api03-" + "A" * 32
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) values (%s, 'add_provider', %s, 'queued')",
            (
                cmd1_id,
                Jsonb({
                    "provider_id": "bad-sec-1",
                    "command": "python",
                    "env": [f"API_KEY={secret_value}"],
                }),
            ),
        )
    status1, res1 = await process_command(pool, cmd1_id)
    assert status1 == "rejected"
    assert "secret" in res1["error"].lower()

    # 2. Raw secret in auth_ref
    cmd2_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) values (%s, 'add_provider', %s, 'queued')",
            (
                cmd2_id,
                Jsonb({
                    "provider_id": "bad-sec-2",
                    "command": "python",
                    "auth_ref": "Bearer " + "secret_token_12345",
                }),
            ),
        )
    status2, res2 = await process_command(pool, cmd2_id)
    assert status2 == "rejected"
    assert "secret" in res2["error"].lower()


async def test_cli_mcp_and_ai_add_parity_and_next_steps(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI commands produce the expected database rows and print next steps."""
    monkeypatch.setenv("FARM_DB_URL", pool.conninfo)
    monkeypatch.setenv("FARM_REGISTRY_PATH", str(tmp_path / "registry.yaml"))

    # 1. farm mcp add
    res_mcp = runner.invoke(
        app,
        [
            "mcp",
            "add",
            "test-cli-mcp",
            "--command",
            "python",
            "--arg",
            "-m",
            "--arg",
            "fake_server",
            "--env",
            "CLI_TEST_VAR",
            "--auth",
            "env",
        ],
    )
    assert res_mcp.exit_code == 0
    assert "Added MCP provider 'test-cli-mcp'" in res_mcp.stdout
    assert "Next step:" in res_mcp.stdout
    assert "farm set-secret CLI_TEST_VAR" in res_mcp.stdout

    async with pool.connection() as conn:
        cur = await conn.execute("select id, kind, executor from public.providers where id = 'test-cli-mcp'")
        row = await cur.fetchone()
        assert row is not None
        assert row[1] == "tool"
        assert row[2] == "mcp"

        cur_conn = await conn.execute("select id, status from public.connections where provider_id = 'test-cli-mcp'")
        conn_row = await cur_conn.fetchone()
        assert conn_row is not None
        assert conn_row[1] == "needs_login"

    # 2. farm ai add
    res_ai = runner.invoke(
        app,
        [
            "ai",
            "add",
            "claude",
            "claude-cli-test",
            "--label",
            "Claude CLI Test",
            "--model",
            "claude-3-7-sonnet",
        ],
    )
    assert res_ai.exit_code == 0
    assert "Added AI provider 'claude' (account: 'claude-cli-test')" in res_ai.stdout
    assert "farm ai login claude-cli-test" in res_ai.stdout

    async with pool.connection() as conn:
        cur_ai = await conn.execute("select id, status from public.connections where id = 'claude-cli-test'")
        ai_row = await cur_ai.fetchone()
        assert ai_row is not None
        assert ai_row[1] == "needs_login"


async def test_remove_provider_guards(pool: DbPool) -> None:
    """remove_provider is refused if active reservations or jobs exist unless --force."""
    # Setup provider + connection
    async with pool.connection() as conn:
        await conn.execute("insert into public.providers (id, name, kind, executor) values ('p_guard', 'Guard Prov', 'ai', 'cli_agent')")
        await conn.execute(
            "insert into public.connections (id, provider_id, label, auth_ref, scope, priority, concurrency, status) "
            "values ('c_guard', 'p_guard', 'Guard Conn', 'cli:c_guard', '{internal}', 100, 1, 'active')"
        )
        # Active ai_job
        conv_id = uuid4()
        await conn.execute(
            "insert into public.ai_conversations (id, ai, account) values (%s, 'p_guard', 'c_guard')",
            (conv_id,),
        )
        job_id = uuid4()
        await conn.execute(
            "insert into public.ai_jobs (id, conversation_id, turn, ai, account, state, caller) "
            "values (%s, %s, 1, 'p_guard', 'c_guard', 'running', 'tester')",
            (job_id, conv_id),
        )
        await conn.commit()

    # Try remove without force
    cmd1_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) values (%s, 'remove_provider', %s, 'queued')",
            (cmd1_id, Jsonb({"provider_id": "p_guard", "force": False})),
        )
    status1, res1 = await process_command(pool, cmd1_id)
    assert status1 == "rejected"
    assert "active" in res1["error"].lower() or "force" in res1["error"].lower()

    # Try remove with force
    cmd2_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) values (%s, 'remove_provider', %s, 'queued')",
            (cmd2_id, Jsonb({"provider_id": "p_guard", "force": True})),
        )
    status2, res2 = await process_command(pool, cmd2_id)
    assert status2 == "done"

    async with pool.connection() as conn:
        cur = await conn.execute("select id from public.providers where id = 'p_guard'")
        assert await cur.fetchone() is None
