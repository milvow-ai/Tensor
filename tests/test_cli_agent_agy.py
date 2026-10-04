"""Tests for Google Antigravity (`agy`) CLI executor."""

import json
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent.agy import AgyCliExecutor
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness() -> FakeCliHarness:
    h = FakeCliHarness()
    yield h
    h.cleanup()


@pytest.mark.asyncio
async def test_agy_success_answer_mode(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("agy")
    payload = {
        "conversation_id": "conv-agy-123",
        "status": "SUCCESS",
        "response": "Gemini response text\n",
        "duration_seconds": 1.25,
        "num_turns": 1,
        "usage": {
            "input_tokens": 500,
            "output_tokens": 80,
            "total_tokens": 580,
        },
    }
    fake_harness.set_response(stdout=json.dumps(payload))

    conn = ConnectionView(
        auth_ref="cli:test",
        id="agy-01",
        provider_id="gemini",
        meta={
            "cli_path": str(bin_path),
            "model": "gemini-3.8-flash-low",
        },
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "explain code", "mode": "answer"},
        connection=conn,
    )

    executor = AgyCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "Gemini response text\n"
    assert res.data["session_id"] == "conv-agy-123"
    assert res.data["model"] == "gemini-3.8-flash-low"
    assert res.units_used["input_tokens"] == 500.0
    assert res.units_used["output_tokens"] == 80.0

    calls = fake_harness.get_calls()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert "-p" in argv
    assert "explain code" in argv
    assert "--output-format" in argv
    assert "json" in argv
    assert "--mode" in argv
    assert "plan" in argv
    assert "--model" in argv
    assert "gemini-3.8-flash-low" in argv


@pytest.mark.asyncio
async def test_agy_edit_mode_and_resume(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("agy")
    payload = {
        "conversation_id": "conv-agy-resume",
        "status": "SUCCESS",
        "response": "Code modified successfully",
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }
    fake_harness.set_response(stdout=json.dumps(payload))

    conn = ConnectionView(
        auth_ref="cli:test",
        id="agy-01",
        provider_id="gemini",
        meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [str(fake_harness.bin_dir)]},
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={
            "task": "refactor function",
            "mode": "edit",
            "session_id": "conv-agy-resume",
            "cwd": str(fake_harness.bin_dir / "workdir"),
        },
        connection=conn,
    )

    executor = AgyCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    calls = fake_harness.get_calls()
    argv = calls[0]["argv"]
    assert "--conversation" in argv
    assert "conv-agy-resume" in argv
    assert "--mode" in argv
    assert "accept-edits" in argv
    assert "--dangerously-skip-permissions" in argv


@pytest.mark.asyncio
async def test_agy_limit_reached(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("agy")
    payload = {
        "status": "ERROR",
        "error": "RESOURCE_EXHAUSTED: quota exceeded. Resets at 2026-10-04T16:00:00Z",
    }
    fake_harness.set_response(stdout=json.dumps(payload), exit_code=1)

    conn = ConnectionView(
        auth_ref="cli:test", id="agy-01", provider_id="gemini", meta={"cli_path": str(bin_path)}
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "big prompt"},
        connection=conn,
    )

    executor = AgyCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.LIMIT_REACHED
    assert res.reset_at is not None
    assert res.reset_at.hour == 16


@pytest.mark.asyncio
async def test_agy_needs_login(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("agy")
    fake_harness.set_response(
        stderr="Authentication required. Please launch agy interactively to authenticate.\n",
        exit_code=1,
    )

    conn = ConnectionView(
        auth_ref="cli:test", id="agy-01", provider_id="gemini", meta={"cli_path": str(bin_path)}
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "check status"},
        connection=conn,
    )

    executor = AgyCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.NEEDS_LOGIN


@pytest.mark.asyncio
async def test_agy_timeout(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("agy")
    fake_harness.set_response(stdout="Delayed response", delay_s=2.0)

    conn = ConnectionView(
        auth_ref="cli:test", id="agy-01", provider_id="gemini", meta={"cli_path": str(bin_path)}
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "slow operation"},
        connection=conn,
        timeout_s=0.2,
    )

    executor = AgyCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.TIMEOUT
