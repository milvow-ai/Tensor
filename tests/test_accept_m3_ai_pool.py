"""Acceptance tests for M3e AI Pool.

Verifies delegating tasks to Claude accounts, Codex, Antigravity, and Hermes through Farm MCP.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.api import ApiExecutor
from farm.executors.cli_agent import CliAgentExecutor
from farm.gateway.server import build_server
from farm.registry.loader import load_registry
from farm.registry.models import CapabilitySpec, ConnectionSpec, ProviderSpec, Registry
from farm.registry.sync import sync_registry
from tests.conftest import FIXTURE_REGISTRY
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[FakeCliHarness]:
    h = FakeCliHarness(tmp_path / "bin")
    h.register_cli("claude")
    h.register_cli("codex")
    h.register_cli("agy")
    h.register_cli("hermes")

    # Prepend fake bin dir to PATH so CLI drivers find the fake executables
    path_env = f"{h.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    monkeypatch.setenv("PATH", path_env)

    yield h
    h.cleanup()


def build_ai_registry(tmp_path: Path) -> Registry:
    """Create a test registry with multiple Claude accounts, agy, and hermes."""
    base = load_registry(FIXTURE_REGISTRY)

    claude_provider = ProviderSpec(
        name="Claude",
        kind="ai",
        executor="cli_agent",
        default_strategy="failover",
        enabled=True,
        connections=[
            ConnectionSpec(
                id="claude-02",
                label="Claude Account 2",
                auth_ref="cli:claude-02",
                priority=1,
                concurrency=1,
                status="active",
                meta={
                    "cli": "claude",
                    "config_dir": str(tmp_path / "claude-02"),
                    "models": ["sonnet", "opus", "haiku"],
                },
            ),
            ConnectionSpec(
                id="claude-03",
                label="Claude Account 3",
                auth_ref="cli:claude-03",
                priority=2,
                concurrency=1,
                status="active",
                meta={
                    "cli": "claude",
                    "config_dir": str(tmp_path / "claude-03"),
                    "models": ["sonnet", "opus", "haiku"],
                },
            ),
            ConnectionSpec(
                id="claude-04",
                label="Claude Account 4",
                auth_ref="cli:claude-04",
                priority=3,
                concurrency=1,
                status="active",
                meta={
                    "cli": "claude",
                    "config_dir": str(tmp_path / "claude-04"),
                    "models": ["sonnet", "opus", "haiku"],
                },
            ),
        ],
    )

    gemini_provider = ProviderSpec(
        name="Gemini (agy)",
        kind="ai",
        executor="cli_agent",
        default_strategy="failover",
        enabled=True,
        connections=[
            ConnectionSpec(
                id="agy-01",
                label="Antigravity Gemini",
                auth_ref="cli:agy-01",
                priority=1,
                concurrency=1,
                status="active",
                meta={
                    "cli": "agy",
                    "models": ["gemini-3.8-flash-low", "gemini-3.8-flash-high"],
                },
            ),
        ],
    )

    hermes_provider = ProviderSpec(
        name="Hermes",
        kind="ai",
        executor="cli_agent",
        default_strategy="failover",
        enabled=True,
        connections=[
            ConnectionSpec(
                id="hermes-01",
                label="Hermes Agent",
                auth_ref="cli:farm-agent",
                priority=1,
                concurrency=1,
                status="active",
                meta={
                    "cli": "hermes",
                    "profile": "farm-agent",
                    "models": ["openrouter/deepseek/deepseek-v4-flash"],
                },
            ),
        ],
    )

    base.providers["claude"] = claude_provider
    base.providers["gemini"] = gemini_provider
    base.providers["hermes"] = hermes_provider

    base.capabilities["ask_ai"] = CapabilitySpec(
        kind="ai",
        description="Ask another AI to do a task.",
        routes=["claude", "gemini", "hermes"],
        strategy="failover",
        cache_ttl_seconds=0,
    )

    return base


@pytest.fixture
async def ai_ctx(pool: DbPool, tmp_path: Path) -> AsyncIterator[FarmContext]:
    reg = build_ai_registry(tmp_path)
    await sync_registry(pool, reg)

    executors: dict[str, Any] = {
        "api": ApiExecutor(),
        "cli_agent": CliAgentExecutor(),
    }
    ctx = FarmContext(pool=pool, executors=executors, poll_interval_s=0.02)
    yield ctx
    await ctx.aclose()


# --------------------------------------------------------------------------------------------------
# Acceptance Case 1: ask_ai over in-memory MCP client
# --------------------------------------------------------------------------------------------------
async def test_case_1_ask_ai_claude_over_mcp(
    ai_ctx: FarmContext, fake_harness: FakeCliHarness, pool: DbPool
) -> None:
    """ask_ai(ai='claude', model='sonnet', task=...) answered by claude-02, trajectory has account + model, answer text not stored in DB."""
    secret_text = "The answer is 42 and only 42!"
    payload = {
        "result": secret_text,
        "session_id": "sess-case-1-abc",
        "total_cost_usd": 0.005,
        "usage": {"input_tokens": 120, "output_tokens": 30},
        "is_error": False,
    }
    fake_harness.set_response(stdout=json.dumps(payload))

    server = await build_server(ai_ctx)
    async with Client(server) as client:
        reply = await client.call_tool(
            "ask_ai",
            {"ai": "claude", "model": "sonnet", "task": "What is the meaning of life?"},
        )
        envelope = reply.structured_content
        assert envelope is not None
        assert envelope["ok"] is True
        assert envelope["result"]["text"] == secret_text
        assert envelope["result"]["session_id"] == "sess-case-1-abc"
        assert envelope["result"]["ai"] == "claude"
        assert envelope["result"]["model"] == "sonnet"
        assert envelope["result"]["connection_id"] == "claude-02"
        assert envelope["source"]["connection_id"] == "claude-02"
        assert envelope["source"]["cached"] is False
        run_id = envelope["run_id"]

        # Trajectory check
        run_info = (await client.call_tool("get_run", {"run_id": run_id})).structured_content
        assert run_info is not None
        assert run_info["connection_id"] == "claude-02"
        kinds = [e["kind"] for e in run_info["events"]]
        assert "execute" in kinds and "success" in kinds
        success_event = next(e for e in run_info["events"] if e["kind"] == "success")
        assert success_event["data"].get("model") == "sonnet"
        assert success_event["data"].get("account") == "claude-02"

    # Verify that nothing of the answer text is stored in the DB (cache_ttl=0)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select count(*) from public.capability_requests where result::text like %s",
            (f"%{secret_text}%",),
        )
        assert (await cur.fetchone())[0] == 0

        cur = await conn.execute(
            "select count(*) from public.run_events where data::text like %s",
            (f"%{secret_text}%",),
        )
        assert (await cur.fetchone())[0] == 0


# --------------------------------------------------------------------------------------------------
# Acceptance Case 2: Batch spreads over >= 2 accounts
# --------------------------------------------------------------------------------------------------
async def test_case_2_batch_spreads_across_accounts(
    ai_ctx: FarmContext, fake_harness: FakeCliHarness
) -> None:
    """Batch of 3 tasks spreads over >= 2 Claude accounts (concurrency 1 each)."""
    payload = {
        "result": "Batch task done",
        "session_id": "sess-batch-test",
        "total_cost_usd": 0.001,
        "is_error": False,
    }
    # delay_s ensures tasks overlap in execution and hit concurrency gating
    fake_harness.set_response(stdout=json.dumps(payload), delay_s=0.25)

    server = await build_server(ai_ctx)
    async with Client(server) as client:
        tasks = [{"ai": "claude", "model": "sonnet", "task": f"Task {i}"} for i in range(3)]
        reply = await client.call_tool("ask_ai_batch", {"tasks": tasks})
        res = reply.structured_content
        assert res is not None
        assert res["ok"] is True
        task_envelopes = res["tasks"]
        assert len(task_envelopes) == 3

        used_accounts = {t["result"]["connection_id"] for t in task_envelopes if t.get("ok")}
        assert len(used_accounts) >= 2, f"Expected tasks to spread across >=2 accounts, got: {used_accounts}"
        assert "claude-02" in used_accounts
        assert "claude-03" in used_accounts


# --------------------------------------------------------------------------------------------------
# Acceptance Case 3: Usage-limit retry + next_reset_at
# --------------------------------------------------------------------------------------------------
async def test_case_3_usage_limit_retries_and_sets_reset_at(
    ai_ctx: FarmContext, fake_harness: FakeCliHarness, pool: DbPool
) -> None:
    """Force claude-02 to return usage-limit error with reset time -> exhausted with next_reset_at, retried on claude-03 and succeeds, list_ais shows reset."""
    reset_ts = "2026-10-05T14:30:00Z"
    limit_payload = {
        "result": f"Usage limit reached. Your limit will reset at {reset_ts}",
        "is_error": True,
    }
    success_payload = {
        "result": "Success from fallback account",
        "session_id": "sess-case-3-fallback",
        "total_cost_usd": 0.002,
        "is_error": False,
    }

    fake_harness.set_response(
        stdout="",
        per_account={
            "claude-02": {"stdout": json.dumps(limit_payload), "exit_code": 0},
            "claude-03": {"stdout": json.dumps(success_payload), "exit_code": 0},
        },
    )

    server = await build_server(ai_ctx)
    async with Client(server) as client:
        reply = await client.call_tool(
            "ask_ai",
            {"ai": "claude", "model": "sonnet", "task": "Do work that limits claude-02"},
        )
        envelope = reply.structured_content
        assert envelope is not None
        assert envelope["ok"] is True
        # Task was retried and succeeded on claude-03
        assert envelope["result"]["connection_id"] == "claude-03"
        assert envelope["result"]["text"] == "Success from fallback account"

        # Check list_ais tool reflects reset time and status
        ais_report = (await client.call_tool("list_ais", {})).structured_content
        assert ais_report is not None
        accounts = ais_report["accounts"]
        c2 = next(a for a in accounts if a["id"] == "claude-02")
        assert c2["status"] == "exhausted"
        assert c2["next_reset_at"] is not None
        # the fake CLI says "resets at <time>": the Farm picks its next occurrence (within a day, in the future)
        reset_at = datetime.fromisoformat(str(c2["next_reset_at"]))
        assert timedelta(0) < reset_at - datetime.now(UTC) <= timedelta(hours=24)

    # Verify in DB: connection status is exhausted
    async with pool.connection() as conn:
        cur = await conn.execute("select status from public.connections where id = 'claude-02'")
        assert (await cur.fetchone())[0] == "exhausted"


# --------------------------------------------------------------------------------------------------
# Acceptance Case 4: Session stickiness, exhausted check, unknown session
# --------------------------------------------------------------------------------------------------
async def test_case_4_session_stickiness_and_guards(
    ai_ctx: FarmContext, fake_harness: FakeCliHarness, pool: DbPool
) -> None:
    """Follow-up with session_id lands on same account (--resume argv asserted); exhausted account -> session_account_unavailable; unknown session -> session_unknown."""
    sess_id = "sess-sticky-444"
    payload_1 = {
        "result": "Initial turn answer",
        "session_id": sess_id,
        "is_error": False,
    }
    payload_2 = {
        "result": "Second turn answer",
        "session_id": sess_id,
        "is_error": False,
    }
    fake_harness.set_response(stdout=json.dumps(payload_1))

    server = await build_server(ai_ctx)
    async with Client(server) as client:
        # Step 1: Initial call lands on claude-02 and establishes session
        reply_1 = await client.call_tool(
            "ask_ai",
            {"ai": "claude", "model": "sonnet", "task": "Turn 1"},
        )
        env_1 = reply_1.structured_content
        assert env_1 is not None and env_1["ok"] is True
        assert env_1["result"]["connection_id"] == "claude-02"

        # Step 2: Follow-up with session_id
        fake_harness.set_response(stdout=json.dumps(payload_2))
        fake_harness.clear_calls()

        reply_2 = await client.call_tool(
            "ask_ai",
            {"ai": "claude", "session_id": sess_id, "task": "Turn 2 follow-up"},
        )
        env_2 = reply_2.structured_content
        assert env_2 is not None and env_2["ok"] is True
        assert env_2["result"]["connection_id"] == "claude-02"

        # Assert --resume was passed to argv
        calls = fake_harness.get_calls()
        assert len(calls) > 0
        last_argv = calls[-1]["argv"]
        assert "--resume" in last_argv
        assert sess_id in last_argv

        # Step 3: If account becomes exhausted, follow-up must fail with session_account_unavailable (no silent fallback)
        async with pool.connection() as conn:
            await conn.execute("update public.connections set status = 'exhausted' where id = 'claude-02'")

        reply_3 = await client.call_tool(
            "ask_ai",
            {"ai": "claude", "session_id": sess_id, "task": "Turn 3 after exhaustion"},
            raise_on_error=False,
        )
        env_3 = reply_3.structured_content
        assert env_3 is not None
        assert env_3["ok"] is False
        assert env_3["error"]["kind"] == "session_account_unavailable"

        # Step 4: Unknown session returns session_unknown
        reply_4 = await client.call_tool(
            "ask_ai",
            {"ai": "claude", "session_id": "non-existent-session-000", "task": "Who am I?"},
            raise_on_error=False,
        )
        env_4 = reply_4.structured_content
        assert env_4 is not None
        assert env_4["ok"] is False
        assert env_4["error"]["kind"] == "session_unknown"


# --------------------------------------------------------------------------------------------------
# Acceptance Case 5: Auth failure -> needs_login + alert row with exact login command
# --------------------------------------------------------------------------------------------------
async def test_case_5_auth_failure_triggers_alert(
    ai_ctx: FarmContext, fake_harness: FakeCliHarness, pool: DbPool
) -> None:
    """Auth failure -> needs_login status + alert row containing 'farm ai login <id>'."""
    auth_err_payload = {
        "result": "Authentication failed. Not logged in.",
        "is_error": True,
    }
    fake_harness.set_response(
        stdout=json.dumps(auth_err_payload),
        stderr="Authentication failed: please log in",
        exit_code=1,
    )

    server = await build_server(ai_ctx)
    async with Client(server) as client:
        # Route to claude-02 (priority 1)
        reply = await client.call_tool(
            "ask_ai",
            {"ai": "claude", "task": "Test auth failure"},
            raise_on_error=False,
        )
        envelope = reply.structured_content
        assert envelope is not None

    # Verify claude-02 was marked needs_login in connections
    async with pool.connection() as conn:
        cur = await conn.execute("select status from public.connections where id = 'claude-02'")
        status = (await cur.fetchone())[0]
        assert status == "needs_login"

        # Verify alert exists with exact command
        cur = await conn.execute("select message from public.alerts where ref = 'login:claude-02'")
        row = await cur.fetchone()
        assert row is not None, "Expected alert row with ref login:claude-02"
        alert_msg = row[0]
        assert "farm ai login claude-02" in alert_msg


# --------------------------------------------------------------------------------------------------
# Acceptance Case 6: Live Smoke for agy-01 and hermes-01
# --------------------------------------------------------------------------------------------------
@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="Live smoke test: run explicitly with RUN_LIVE_TESTS=1",
)
def test_case_6_live_smoke() -> None:
    """Live smoke test via CLI: farm ai test agy-01 and farm ai test hermes-01."""
    import subprocess
    import sys

    res_agy = subprocess.run(
        [sys.executable, "-c", "from farm.control.cli import app; app()", "ai", "test", "agy-01", "--local"],
        capture_output=True,
        text=True,
    )
    assert res_agy.returncode == 0, f"agy-01 test failed: {res_agy.stdout}\n{res_agy.stderr}"
    assert "OK: agy-01 answered:" in res_agy.stdout
    print(f"\n[LIVE agy-01 Output]: {res_agy.stdout.strip()}")

    res_hermes = subprocess.run(
        [
            sys.executable,
            "-c",
            "from farm.control.cli import app; app()",
            "ai",
            "test",
            "hermes-01",
            "--local",
        ],
        capture_output=True,
        text=True,
    )
    assert res_hermes.returncode == 0, f"hermes-01 test failed: {res_hermes.stdout}\n{res_hermes.stderr}"
    assert "OK: hermes-01 answered:" in res_hermes.stdout
    print(f"\n[LIVE hermes-01 Output]: {res_hermes.stdout.strip()}")
