"""Tests for base CLI agent executor utilities and subprocess runner."""

import asyncio
import sys
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psutil
import pytest

from farm.executors.base import ConnectionView
from farm.executors.cli_agent.base import (
    BaseCliAgentExecutor,
    clean_cli_text,
    extract_cost_usd,
    extract_token_usage,
    parse_json_or_none,
    parse_jsonl,
    parse_reset_at,
    run_cli_process,
)


def test_clean_cli_text() -> None:
    raw = "\x1b[32mSuccess\x1b[0m: operation finished\n"
    assert clean_cli_text(raw) == "Success: operation finished"


def test_parse_json_or_none() -> None:
    # Direct JSON
    assert parse_json_or_none('{"key": "value"}') == {"key": "value"}
    # Embedded in noise
    text = 'Some logging before\n{"result": "ok", "code": 200}\nSome trailing output'
    assert parse_json_or_none(text) == {"result": "ok", "code": 200}
    # List JSON
    assert parse_json_or_none("[1, 2, 3]") == [1, 2, 3]
    # Invalid JSON
    assert parse_json_or_none("Not json at all") is None
    assert parse_json_or_none("") is None


def test_parse_jsonl() -> None:
    lines = '{"type": "init"}\nnot a json line\n{"type": "message", "text": "hello"}\n'
    parsed = parse_jsonl(lines)
    assert len(parsed) == 2
    assert parsed[0]["type"] == "init"
    assert parsed[1]["text"] == "hello"


def test_parse_reset_at() -> None:
    # From data dict with ISO string
    data = {"reset_at": "2026-10-04T18:30:00Z"}
    dt = parse_reset_at("", data)
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.month == 10 and dt.day == 4

    # From data dict with timestamp
    data2 = {"next_reset_at": 1791115200}
    dt2 = parse_reset_at("", data2)
    assert dt2 is not None

    # From text containing ISO timestamp
    text = "Error 429: Usage limit reached. Resets at 2026-10-04T22:00:00+00:00 for your tier."
    dt3 = parse_reset_at(text)
    assert dt3 is not None
    assert dt3.hour == 22

    # From relative duration in text
    text_rel = "Rate limit exceeded. Resets in 2 hours."
    dt4 = parse_reset_at(text_rel)
    assert dt4 is not None
    now = datetime.now(UTC)
    diff_s = (dt4 - now).total_seconds()
    assert 7100 < diff_s < 7300


def test_extract_token_usage_and_cost() -> None:
    data = {
        "usage": {
            "input_tokens": 1500,
            "output_tokens": 300,
            "reasoning_tokens": 50,
            "cache_read_tokens": 200,
        },
        "total_cost_usd": 0.045,
    }
    usage = extract_token_usage(data)
    assert usage["input_tokens"] == 1500.0
    assert usage["output_tokens"] == 300.0
    assert usage["reasoning_tokens"] == 50.0
    assert usage["cache_read_tokens"] == 200.0

    cost = extract_cost_usd(data)
    assert cost == Decimal("0.045")


@pytest.mark.asyncio
async def test_run_cli_process_basic() -> None:
    py_code = 'import sys; print("hello from stdout"); sys.stderr.write("hello from stderr\\n")'
    out = await run_cli_process([sys.executable, "-c", py_code])
    assert out.exit_code == 0
    assert "hello from stdout" in out.stdout
    assert "hello from stderr" in out.stderr
    assert not out.timed_out
    assert out.duration_s > 0


@pytest.mark.asyncio
async def test_run_cli_process_env_and_cwd() -> None:
    with tempfile.TemporaryDirectory() as td:
        py_code = "import os; print(os.environ.get('MY_TEST_VAR', '')); print(os.getcwd())"
        out = await run_cli_process(
            [sys.executable, "-c", py_code],
            cwd=td,
            env={"MY_TEST_VAR": "secret_test_val"},
        )
        assert out.exit_code == 0
        lines = out.stdout.strip().splitlines()
        assert lines[0] == "secret_test_val"
        assert Path(lines[1]).resolve() == Path(td).resolve()


@pytest.mark.asyncio
async def test_run_cli_process_timeout() -> None:
    py_code = "import time; time.sleep(10)"
    out = await run_cli_process(
        [sys.executable, "-c", py_code],
        timeout_s=0.2,
    )
    assert out.timed_out
    assert out.exit_code == -1
    assert "timed out" in out.stderr


@pytest.mark.asyncio
async def test_run_cli_process_stdout_cap() -> None:
    # Print 200KB with 10KB cap
    py_code = 'import sys; sys.stdout.write("A" * 200000)'
    out = await run_cli_process(
        [sys.executable, "-c", py_code],
        max_bytes=10000,
    )
    assert out.exit_code == 0
    assert "[OUTPUT TRUNCATED]" in out.stdout
    assert len(out.stdout) < 20000


def test_build_exec_result() -> None:
    conn = ConnectionView(auth_ref="cli:test", id="test-conn", provider_id="test-prov")
    res = BaseCliAgentExecutor.build_exec_result(
        ok=True,
        text="All done",
        session_id="sess-123",
        ai="claude",
        model="sonnet",
        connection=conn,
        raw_data={"usage": {"input_tokens": 10}, "total_cost_usd": 0.01},
        duration_s=1.5,
    )
    assert res.ok
    assert res.data is not None
    assert res.data["text"] == "All done"
    assert res.data["session_id"] == "sess-123"
    assert res.cost_usd == Decimal("0.01")
    assert res.units_used["input_tokens"] == 10.0
    assert res.latency_ms == 1500


def test_selector_loop_cli_runner() -> None:
    """Run CLI runner under an explicit SelectorEventLoop on Windows (and default elsewhere).

    Asserts:
    1. A fake CLI succeeds.
    2. A fake CLI that sleeps beyond timeout is killed together with a spawned child (child PID is gone).
    3. Cancellation of the awaiting task kills the process tree (child PID is gone).
    """
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None

    async def _test() -> None:
        current_loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            assert isinstance(current_loop, asyncio.SelectorEventLoop)

        # 1. Fake CLI succeeds
        out = await run_cli_process([sys.executable, "-c", "print('fake CLI success')"])
        assert out.exit_code == 0
        assert "fake CLI success" in out.stdout
        assert not out.timed_out

        # 2. Fake CLI that sleeps beyond timeout is killed together with its spawned child
        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "timeout_child.pid"
            parent_script = (
                "import subprocess, sys, time; "
                f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                f"open('{pid_file.as_posix()}', 'w').write(str(p.pid)); "
                "time.sleep(60)"
            )
            out_timeout = await run_cli_process(
                [sys.executable, "-c", parent_script],
                timeout_s=1.0,
            )
            assert out_timeout.timed_out
            assert out_timeout.exit_code == -1
            assert "timed out" in out_timeout.stderr

            assert pid_file.exists(), "Child PID file was not created"
            child_pid = int(pid_file.read_text().strip())

            # Assert the child PID is gone
            deadline = time.monotonic() + 5.0
            child_alive = True
            while time.monotonic() < deadline:
                if not psutil.pid_exists(child_pid):
                    child_alive = False
                    break
                try:
                    proc = psutil.Process(child_pid)
                    if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                        child_alive = False
                        break
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    child_alive = False
                    break
                await asyncio.sleep(0.1)

            assert not child_alive, f"Child PID {child_pid} was not killed on timeout"

        # 3. Cancellation kills the process tree
        with tempfile.TemporaryDirectory() as td:
            pid_file = Path(td) / "cancel_child.pid"
            parent_script = (
                "import subprocess, sys, time; "
                f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                f"open('{pid_file.as_posix()}', 'w').write(str(p.pid)); "
                "time.sleep(60)"
            )
            task = asyncio.create_task(
                run_cli_process([sys.executable, "-c", parent_script], timeout_s=30.0)
            )

            # Wait until child process is running and PID is written
            deadline = time.monotonic() + 5.0
            child_pid = None
            while time.monotonic() < deadline:
                if pid_file.exists():
                    text = pid_file.read_text().strip()
                    if text.isdigit():
                        child_pid = int(text)
                        break
                await asyncio.sleep(0.05)

            assert child_pid is not None, "Child PID was not written in time"
            assert psutil.pid_exists(child_pid), f"Child PID {child_pid} should be alive initially"

            # Cancel the awaiting task
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # Assert the child PID is gone
            deadline = time.monotonic() + 5.0
            child_alive = True
            while time.monotonic() < deadline:
                if not psutil.pid_exists(child_pid):
                    child_alive = False
                    break
                try:
                    proc = psutil.Process(child_pid)
                    if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                        child_alive = False
                        break
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    child_alive = False
                    break
                await asyncio.sleep(0.1)

            assert not child_alive, f"Child PID {child_pid} was not killed on task cancellation"

    asyncio.run(_test(), loop_factory=loop_factory)

