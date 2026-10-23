"""Q0B: end-to-end through the executors (fake CLI) - real recorded outputs must not be falsely flagged."""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from tests.test_cli_agent_fakes import FakeCliHarness

FIX = Path(__file__).parent / "fixtures" / "cli"


@pytest.fixture
def harness() -> Iterator[FakeCliHarness]:
    h = FakeCliHarness()
    yield h
    h.cleanup()


def _req(cli: str, bin_path: Path, provider: str) -> ExecRequest:
    conn = ConnectionView(auth_ref="cli:test", id=f"{cli}-01", provider_id=provider, meta={"cli_path": str(bin_path)})
    return ExecRequest(request_id=uuid4(), capability="ask_ai", params={"task": "x"}, connection=conn)


def _replay(h: FakeCliHarness, cli: str, case: str) -> None:
    base = FIX / cli / case
    h.set_response(
        stdout=base.with_suffix(".stdout").read_text(encoding="utf-8"),
        stderr=base.with_suffix(".stderr").read_text(encoding="utf-8"),
        exit_code=int(base.with_suffix(".exit").read_text(encoding="utf-8").strip()),
    )


async def _run(h: FakeCliHarness, cli: str, case: str) -> ExecResult:
    bin_path = h.register_cli(cli)
    _replay(h, cli, case)
    if cli == "claude":
        return await ClaudeCliExecutor().execute(_req(cli, bin_path, "claude"))
    return await AgyCliExecutor().execute(_req(cli, bin_path, "gemini"))


@pytest.mark.asyncio
@pytest.mark.parametrize("cli", ["claude", "agy"])
@pytest.mark.parametrize("case", ["success", "success_trigger_words"])
async def test_successful_run_is_not_flagged(harness: FakeCliHarness, cli: str, case: str) -> None:
    res = await _run(harness, cli, case)
    assert res.ok, res.error
    assert res.error_kind is None


@pytest.mark.asyncio
async def test_claude_not_logged_in_needs_login(harness: FakeCliHarness) -> None:
    res = await _run(harness, "claude", "not_logged_in")
    assert not res.ok
    assert res.error_kind == ErrorKind.NEEDS_LOGIN


@pytest.mark.asyncio
async def test_agy_quota_is_limit_with_reset(harness: FakeCliHarness) -> None:
    res = await _run(harness, "agy", "limit")
    assert res.error_kind == ErrorKind.LIMIT_REACHED
    assert res.reset_at is not None
