"""Tests for C3 commands in farm/control/commands.py:

- cancel_ai_job
- set_max_parallel
- set_mcp_tool_access
- sync_mcp_tools
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from farm.control.commands import process_command
from farm.db.pool import DbPool
from tests.conftest import seed_connection

VIEWS_C3_SQL_PATH = Path(__file__).parent.parent / "console" / "sql" / "views_c3.sql"


@pytest.fixture(autouse=True)
async def setup_c3_schema(pool: DbPool) -> None:
    c3_sql = VIEWS_C3_SQL_PATH.read_text(encoding="utf-8")
    async with pool.connection() as conn:
        await conn.execute(c3_sql)


@pytest.mark.asyncio
async def test_cancel_ai_job_command(pool: DbPool) -> None:
    conv_id = uuid4()
    job_id = uuid4()

    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.ai_conversations (id, ai, account, turns, tokens, cost)
            values (%s, 'claude', 'claude-01', 0, 0, 0)
            """,
            (conv_id,),
        )
        await conn.execute(
            """
            insert into public.ai_jobs (id, conversation_id, turn, ai, account, state)
            values (%s, %s, 1, 'claude', 'claude-01', 'queued')
            """,
            (job_id, conv_id),
        )

    # 1. Process cancel command
    cmd_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.farm_commands (id, kind, payload, status, created_by)
            values (%s, 'cancel_ai_job', %s, 'queued', 'console')
            """,
            (cmd_id, Jsonb({"job_id": str(job_id)})),
        )

    status, result = await process_command(pool, cmd_id)
    assert status == "done"
    assert result["job_id"] == str(job_id)
    assert result["cancelled"] is True

    # Verify DB state
    async with pool.connection() as conn:
        cur = await conn.execute("select state, error_kind from public.ai_jobs where id = %s", (job_id,))
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "cancelled"
        assert row[1] == "cancelled"

        # Check audit event
        cur_audit = await conn.execute(
            "select action, target from public.audit_events where action = 'cancel_ai_job' and target = %s",
            (str(job_id),),
        )
        assert await cur_audit.fetchone() is not None

    # 2. Reject non-existent job
    missing_cmd_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.farm_commands (id, kind, payload, status, created_by)
            values (%s, 'cancel_ai_job', %s, 'queued', 'console')
            """,
            (missing_cmd_id, Jsonb({"job_id": str(uuid4())})),
        )
    status_m, result_m = await process_command(pool, missing_cmd_id)
    assert status_m == "rejected"
    assert "not found" in result_m["error"]


@pytest.mark.asyncio
async def test_set_max_parallel_command(pool: DbPool) -> None:
    await seed_connection(
        pool,
        "p_ai_test",
        "c_ai_1",
        units={"credits": 100},
        concurrency=1,
    )

    # 1. Update connection max_parallel
    cmd_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.farm_commands (id, kind, payload, status, created_by)
            values (%s, 'set_max_parallel', %s, 'queued', 'console')
            """,
            (cmd_id, Jsonb({"connection_id": "c_ai_1", "max_parallel": 4})),
        )

    status, result = await process_command(pool, cmd_id)
    assert status == "done"
    assert result["max_parallel"] == 4

    async with pool.connection() as conn:
        cur = await conn.execute("select concurrency, meta from public.connections where id = 'c_ai_1'")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 4
        assert row[1].get("max_parallel") == 4

    # 2. Update provider pool max_parallel
    cmd_p_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.farm_commands (id, kind, payload, status, created_by)
            values (%s, 'set_max_parallel', %s, 'queued', 'console')
            """,
            (cmd_p_id, Jsonb({"provider_id": "p_ai_test", "max_parallel": 10})),
        )

    status_p, result_p = await process_command(pool, cmd_p_id)
    assert status_p == "done"
    assert result_p["max_parallel"] == 10

    async with pool.connection() as conn:
        cur = await conn.execute("select config from public.providers where id = 'p_ai_test'")
        row = await cur.fetchone()
        assert row is not None
        assert row[0].get("max_parallel") == 10


@pytest.mark.asyncio
async def test_set_mcp_tool_access_command(pool: DbPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.providers (id, name, kind, executor, default_strategy, enabled, config)
            values ('p_mcp_test', 'MCP Server', 'tool', 'mcp', 'failover', true,
                    '{"mcp": {"transport": "stdio", "command": "npx echo", "tools": {"allow": ["*"], "deny": []}}}'::jsonb)
            """
        )

    # 1. Deny tool
    cmd_deny = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.farm_commands (id, kind, payload, status, created_by)
            values (%s, 'set_mcp_tool_access', %s, 'queued', 'console')
            """,
            (cmd_deny, Jsonb({"provider_id": "p_mcp_test", "tool": "dangerous_action", "enabled": False})),
        )

    status_d, res_d = await process_command(pool, cmd_deny)
    assert status_d == "done"
    assert res_d["enabled"] is False

    async with pool.connection() as conn:
        cur = await conn.execute("select config from public.providers where id = 'p_mcp_test'")
        row = await cur.fetchone()
        assert row is not None
        deny_list = row[0]["mcp"]["tools"]["deny"]
        assert "dangerous_action" in deny_list

    # 2. Re-allow tool
    cmd_allow = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.farm_commands (id, kind, payload, status, created_by)
            values (%s, 'set_mcp_tool_access', %s, 'queued', 'console')
            """,
            (cmd_allow, Jsonb({"provider_id": "p_mcp_test", "tool": "dangerous_action", "enabled": True})),
        )

    status_a, res_a = await process_command(pool, cmd_allow)
    assert status_a == "done"
    assert res_a["enabled"] is True

    async with pool.connection() as conn:
        cur = await conn.execute("select config from public.providers where id = 'p_mcp_test'")
        row = await cur.fetchone()
        assert row is not None
        deny_list = row[0]["mcp"]["tools"]["deny"]
        assert "dangerous_action" not in deny_list


@pytest.mark.asyncio
async def test_sync_mcp_tools_command(pool: DbPool) -> None:
    # sync_mcp_tools with non-existent or empty provider
    cmd_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.farm_commands (id, kind, payload, status, created_by)
            values (%s, 'sync_mcp_tools', %s, 'queued', 'console')
            """,
            (cmd_id, Jsonb({"provider_id": "p_nonexistent"})),
        )

    status, res = await process_command(pool, cmd_id)
    assert status in ("done", "failed")
    assert "results" in res
