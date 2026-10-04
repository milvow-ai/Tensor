"""Tests for Claude CLI executor."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness() -> FakeCliHarness:
    h = FakeCliHarness()
    yield h
    h.cleanup()


@pytest.mark.asyncio
async def test_claude_success_answer_mode(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("claude")
    payload = {
        "result": "Hello world from Claude",
        "session_id": "session-123",
        "total_cost_usd": 0.015,
        "usage": {"input_tokens": 120, "output_tokens": 40},
        "is_error": False,
    }
    fake_harness.set_response(stdout=json.dumps(payload))

    conn = ConnectionView(
        id="claude-02",
        provider_id="claude",
        meta={
            "cli_path": str(bin_path),
            "config_dir": "D:/farm-data/ai/claude-02",
            "model": "sonnet",
        },
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "say hello", "mode": "answer"},
        connection=conn,
    )

    executor = ClaudeCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "Hello world from Claude"
    assert res.data["session_id"] == "session-123"
    assert res.data["model"] == "sonnet"
    assert res.cost_usd == Decimal("0.015")
    assert res.units_used["input_tokens"] == 120.0
    assert res.units_used["output_tokens"] == 40.0

    calls = fake_harness.get_calls()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert "-p" in argv
    assert "say hello" in argv
    assert "--output-format" in argv
    assert "json" in argv
    assert "--model" in argv
    assert "sonnet" in argv
    assert "--allowedTools" in argv
    assert calls[0]["env"]["CLAUDE_CONFIG_DIR"] == "D:/farm-data/ai/claude-02"


@pytest.mark.asyncio
async def test_claude_edit_mode_and_resume(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("claude")
    payload = {
        "result": "Edited file successfully",
        "session_id": "session-resume-456",
        "is_error": False,
    }
    fake_harness.set_response(stdout=json.dumps(payload))

    conn = ConnectionView(
        id="claude-03",
        provider_id="claude",
        meta={"cli_path": str(bin_path)},
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={
            "task": "modify code",
            "mode": "edit",
            "session_id": "session-resume-456",
            "model": "opus",
        },
        connection=conn,
    )

    executor = ClaudeCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    calls = fake_harness.get_calls()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert "--permission-mode" in argv
    assert "acceptEdits" in argv
    assert "--resume" in argv
    assert "session-resume-456" in argv
    assert "--model" in argv
    assert "opus" in argv


@pytest.mark.asyncio
async def test_claude_limit_reached_with_reset(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("claude")
    payload = {
        "is_error": True,
        "result": "You have hit your usage limit. Resets at 2026-10-04T18:00:00Z",
    }
    fake_harness.set_response(stdout=json.dumps(payload), exit_code=1)

    conn = ConnectionView(id="claude-02", provider_id="claude", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "expensive prompt"},
        connection=conn,
    )

    executor = ClaudeCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.LIMIT_REACHED
    assert res.reset_at is not None
    assert res.reset_at.hour == 18


@pytest.mark.asyncio
async def test_claude_needs_login(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("claude")
    fake_harness.set_response(
        stderr="Authentication required: please log in with claude login\n",
        exit_code=1,
    )

    conn = ConnectionView(id="claude-02", provider_id="claude", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "whoami"},
        connection=conn,
    )

    executor = ClaudeCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.NEEDS_LOGIN


@pytest.mark.asyncio
async def test_claude_timeout(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("claude")
    fake_harness.set_response(stdout="Too slow", delay_s=2.0)

    conn = ConnectionView(id="claude-02", provider_id="claude", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "sleep", "timeout_s": 0.2},
        connection=conn,
        timeout_s=0.2,
    )

    executor = ClaudeCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.TIMEOUT


@pytest.mark.asyncio
async def test_claude_json_schema(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("claude")
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    payload = {
        "result": '{"name": "Alice"}',
        "session_id": "sess-schema",
        "is_error": False,
    }
    fake_harness.set_response(stdout=json.dumps(payload))

    conn = ConnectionView(id="claude-02", provider_id="claude", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "output name", "json_schema": schema},
        connection=conn,
    )

    executor = ClaudeCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["json"] == {"name": "Alice"}
    calls = fake_harness.get_calls()
    argv = calls[0]["argv"]
    assert "--json-schema" in argv
