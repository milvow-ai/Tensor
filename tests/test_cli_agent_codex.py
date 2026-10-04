"""Tests for OpenAI Codex CLI executor."""

import json
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent.codex import CodexCliExecutor
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness() -> FakeCliHarness:
    h = FakeCliHarness()
    yield h
    h.cleanup()


@pytest.mark.asyncio
async def test_codex_success_answer_mode_jsonl(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("codex")
    events = [
        {"type": "thread.created", "thread_id": "thread-101"},
        {"type": "message", "role": "assistant", "content": "Codex solution found"},
        {"type": "turn.finished", "usage": {"input_tokens": 80, "output_tokens": 25}},
    ]
    stdout_lines = "\n".join(json.dumps(ev) for ev in events)
    fake_harness.set_response(stdout=stdout_lines)

    conn = ConnectionView(
        id="codex-01",
        provider_id="codex",
        meta={
            "cli_path": str(bin_path),
            "config_dir": "D:/farm-data/ai/codex-01",
            "model": "o3",
        },
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "implement algorithm", "mode": "answer"},
        connection=conn,
    )

    executor = CodexCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "Codex solution found"
    assert res.data["session_id"] == "thread-101"
    assert res.data["model"] == "o3"
    assert res.units_used["input_tokens"] == 80.0
    assert res.units_used["output_tokens"] == 25.0

    calls = fake_harness.get_calls()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert "exec" in argv
    assert "--json" in argv
    assert "--skip-git-repo-check" in argv
    assert "--sandbox" in argv
    assert "read-only" in argv
    assert "-m" in argv
    assert "o3" in argv
    assert "implement algorithm" in argv
    assert calls[0]["env"]["CODEX_HOME"] == "D:/farm-data/ai/codex-01"


@pytest.mark.asyncio
async def test_codex_edit_mode_and_resume(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("codex")
    events = [
        {"type": "session.resumed", "session_id": "thread-resume-789"},
        {"type": "item.completed", "item": {"type": "message", "text": "Files updated"}},
    ]
    stdout_lines = "\n".join(json.dumps(ev) for ev in events)
    fake_harness.set_response(stdout=stdout_lines)

    conn = ConnectionView(id="codex-01", provider_id="codex", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={
            "task": "apply fixes",
            "mode": "edit",
            "session_id": "thread-resume-789",
        },
        connection=conn,
    )

    executor = CodexCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "Files updated"
    assert res.data["session_id"] == "thread-resume-789"

    calls = fake_harness.get_calls()
    argv = calls[0]["argv"]
    assert "exec" in argv
    assert "resume" in argv
    assert "thread-resume-789" in argv
    assert "--sandbox" in argv
    assert "workspace-write" in argv


@pytest.mark.asyncio
async def test_codex_single_json_response(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("codex")
    payload = {
        "result": "Single json output from codex",
        "session_id": "sess-codex-simple",
        "usage": {"input_tokens": 50, "output_tokens": 10},
    }
    fake_harness.set_response(stdout=json.dumps(payload))

    conn = ConnectionView(id="codex-01", provider_id="codex", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "say hello"},
        connection=conn,
    )

    executor = CodexCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "Single json output from codex"
    assert res.data["session_id"] == "sess-codex-simple"


@pytest.mark.asyncio
async def test_codex_limit_reached(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("codex")
    fake_harness.set_response(
        stderr="Error: rate limit exceeded (insufficient_quota). Resets at 2026-10-04T23:00:00Z\n",
        exit_code=1,
    )

    conn = ConnectionView(id="codex-01", provider_id="codex", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "generate code"},
        connection=conn,
    )

    executor = CodexCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.LIMIT_REACHED
    assert res.reset_at is not None
    assert res.reset_at.hour == 23


@pytest.mark.asyncio
async def test_codex_needs_login(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("codex")
    fake_harness.set_response(
        stderr="Codex error: Not logged in. Please run `codex login` to authenticate.\n",
        exit_code=1,
    )

    conn = ConnectionView(id="codex-01", provider_id="codex", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "list files"},
        connection=conn,
    )

    executor = CodexCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.NEEDS_LOGIN


@pytest.mark.asyncio
async def test_codex_timeout(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("codex")
    fake_harness.set_response(stdout="Hanging", delay_s=2.0)

    conn = ConnectionView(id="codex-01", provider_id="codex", meta={"cli_path": str(bin_path)})
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "run slow command"},
        connection=conn,
        timeout_s=0.2,
    )

    executor = CodexCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.TIMEOUT
