"""Tests for Hermes CLI executor."""

from decimal import Decimal
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent.hermes import HermesCliExecutor
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness() -> FakeCliHarness:
    h = FakeCliHarness()
    yield h
    h.cleanup()


@pytest.mark.asyncio
async def test_hermes_success_basic(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("hermes")
    usage_data = {
        "estimated_cost_usd": 0.00025,
        "cost_status": "estimated",
        "session_id": "hermes-sess-001",
        "input_tokens": 1200,
        "output_tokens": 50,
        "reasoning_tokens": 10,
        "total_tokens": 1260,
        "completed": True,
        "failed": False,
        "model": "openrouter/deepseek/deepseek-v4-flash",
    }
    fake_harness.set_response(
        stdout="Hermes task output\n",
        usage_file_data=usage_data,
    )

    conn = ConnectionView(
        auth_ref="cli:test",
        id="hermes-01",
        provider_id="hermes",
        meta={
            "cli_path": str(bin_path),
            "profile": "farm-agent",
            "model": "openrouter/deepseek/deepseek-v4-flash",
        },
    )
    test_cwd = str(fake_harness.bin_dir / "workdir")
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "research brief", "mode": "answer", "cwd": test_cwd},
        connection=conn,
    )

    executor = HermesCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "Hermes task output"
    assert res.data["session_id"] == "hermes-sess-001"
    assert res.data["model"] == "openrouter/deepseek/deepseek-v4-flash"
    assert res.cost_usd == Decimal("0.00025")
    assert res.units_used["input_tokens"] == 1200.0
    assert res.units_used["output_tokens"] == 50.0

    calls = fake_harness.get_calls()
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert "-p" in argv
    assert "farm-agent" in argv
    assert "-z" in argv
    assert "research brief" in argv
    assert "--usage-file" in argv
    assert "--in" in argv
    assert test_cwd in argv
    assert calls[0]["env"]["TERMINAL_CWD"] == test_cwd


@pytest.mark.asyncio
async def test_hermes_resume_and_model(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("hermes")
    usage_data = {
        "session_id": "hermes-sess-resume",
        "completed": True,
    }
    fake_harness.set_response(
        stdout="Continuation answer",
        usage_file_data=usage_data,
    )

    conn = ConnectionView(
        auth_ref="cli:test", id="hermes-01", provider_id="hermes", meta={"cli_path": str(bin_path)}
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={
            "task": "continue work",
            "session_id": "hermes-sess-resume",
            "model": "anthropic/claude-sonnet-4.6",
        },
        connection=conn,
    )

    executor = HermesCliExecutor()
    res = await executor.execute(req)

    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "Continuation answer"
    assert res.data["session_id"] == "hermes-sess-resume"

    calls = fake_harness.get_calls()
    argv = calls[0]["argv"]
    assert "--resume" in argv
    assert "hermes-sess-resume" in argv
    assert "-m" in argv
    assert "anthropic/claude-sonnet-4.6" in argv


@pytest.mark.asyncio
async def test_hermes_limit_reached(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("hermes")
    fake_harness.set_response(
        stderr="Error: insufficient balance on provider key. Resets at 2026-10-04T15:00:00Z\n",
        exit_code=1,
    )

    conn = ConnectionView(
        auth_ref="cli:test", id="hermes-01", provider_id="hermes", meta={"cli_path": str(bin_path)}
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "expensive run"},
        connection=conn,
    )

    executor = HermesCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.LIMIT_REACHED
    assert res.reset_at is not None
    assert res.reset_at.hour == 15


@pytest.mark.asyncio
async def test_hermes_needs_login(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("hermes")
    fake_harness.set_response(
        stderr="Hermes authentication error: Invalid API key or unauthorized.\n",
        exit_code=1,
    )

    conn = ConnectionView(
        auth_ref="cli:test", id="hermes-01", provider_id="hermes", meta={"cli_path": str(bin_path)}
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "check auth"},
        connection=conn,
    )

    executor = HermesCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.NEEDS_LOGIN


@pytest.mark.asyncio
async def test_hermes_timeout(fake_harness: FakeCliHarness) -> None:
    bin_path = fake_harness.register_cli("hermes")
    fake_harness.set_response(stdout="Late output", delay_s=2.0)

    conn = ConnectionView(
        auth_ref="cli:test", id="hermes-01", provider_id="hermes", meta={"cli_path": str(bin_path)}
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "long operation"},
        connection=conn,
        timeout_s=0.2,
    )

    executor = HermesCliExecutor()
    res = await executor.execute(req)

    assert not res.ok
    assert res.error_kind == ErrorKind.TIMEOUT
