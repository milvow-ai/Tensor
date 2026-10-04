"""Security tests for CLI task delivery (stdin, argv, no cmd.exe, no overflow) (Requirement 3).

1. Claude and Codex deliver task text via stdin.
2. Agv metacharacters (& echo injected > marker.txt) arrive byte-exact without executing in cmd.exe.
3. 60,000-char task arrives intact for stdin-capable CLIs, or returns clear BAD_REQUEST for argv-only CLIs.
4. Batch files (.cmd/.bat) are resolved to their target binary or refused.
"""

import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.base import resolve_shim, run_cli_process
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from farm.executors.cli_agent.codex import CodexCliExecutor
from farm.executors.cli_agent.hermes import HermesCliExecutor
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness() -> FakeCliHarness:
    h = FakeCliHarness()
    yield h
    h.cleanup()


@pytest.mark.asyncio
async def test_cmd_metacharacter_injection_does_not_execute(fake_harness: FakeCliHarness) -> None:
    """Task containing '& echo injected > marker.txt' must arrive byte-exact without running commands."""
    with tempfile.TemporaryDirectory() as td:
        marker_path = Path(td) / "marker.txt"
        injection_task = f"legitimate prompt & echo injected > \"{marker_path}\""

        drivers = [
            ("claude", ClaudeCliExecutor(), True),
            ("codex", CodexCliExecutor(), True),
            ("agy", AgyCliExecutor(), False),
            ("hermes", HermesCliExecutor(), False),
        ]

        for name, executor, uses_stdin in drivers:
            fake_harness.clear_calls()
            bin_path = fake_harness.register_cli(name)

            if name == "claude":
                fake_harness.set_response(stdout=json.dumps({"result": "ok", "is_error": False}))
            elif name == "codex":
                fake_harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "ok"}}))
            elif name == "agy":
                fake_harness.set_response(stdout=json.dumps({"status": "SUCCESS", "response": "ok"}))
            elif name == "hermes":
                fake_harness.set_response(stdout="ok", usage_file_data={"estimated_cost_usd": 0.001})

            conn = ConnectionView(
                auth_ref="cli:test",
                id=f"{name}-inj",
                provider_id=name,
                meta={"cli_path": str(bin_path)},
            )
            req = ExecRequest(
                request_id=uuid4(),
                capability="ask_ai",
                params={"task": injection_task, "mode": "answer"},
                connection=conn,
            )

            res = await executor.execute(req)
            assert res.ok, f"Execution failed for {name}: {res.error}"

            # Marker file must NEVER be created
            assert not marker_path.exists(), f"Command injection succeeded for {name}! marker.txt was created"

            calls = fake_harness.get_calls()
            assert len(calls) == 1

            if uses_stdin:
                # Prompt arrived via stdin
                assert calls[0]["stdin"] == injection_task
                # Prompt was NOT in raw argv
                assert injection_task not in calls[0]["raw_argv"]
            else:
                # Prompt arrived byte-exact in argv without shell evaluation
                assert injection_task in calls[0]["argv"]


@pytest.mark.asyncio
async def test_large_task_handling(fake_harness: FakeCliHarness) -> None:
    """60,000-char task arrives intact for stdin CLIs, returns BAD_REQUEST for argv-only CLIs."""
    long_task = "Task content: " + ("0123456789" * 6000)  # 60,014 chars

    # 1. Claude (stdin support)
    bin_path = fake_harness.register_cli("claude")
    fake_harness.set_response(stdout=json.dumps({"result": "done", "is_error": False}))
    conn = ConnectionView(auth_ref="cli:test", id="claude-large", provider_id="claude", meta={"cli_path": str(bin_path)})
    req = ExecRequest(request_id=uuid4(), capability="ask_ai", params={"task": long_task}, connection=conn)

    res = await ClaudeCliExecutor().execute(req)
    assert res.ok, f"Claude failed on 60k task: {res.error}"
    calls = fake_harness.get_calls()
    assert len(calls) == 1
    assert calls[0]["stdin"] == long_task
    assert long_task not in calls[0]["raw_argv"]

    # 2. Codex (stdin support)
    fake_harness.clear_calls()
    bin_path = fake_harness.register_cli("codex")
    fake_harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "done"}}))
    conn = ConnectionView(auth_ref="cli:test", id="codex-large", provider_id="codex", meta={"cli_path": str(bin_path)})
    req = ExecRequest(request_id=uuid4(), capability="ask_ai", params={"task": long_task}, connection=conn)

    res = await CodexCliExecutor().execute(req)
    assert res.ok, f"Codex failed on 60k task: {res.error}"
    calls = fake_harness.get_calls()
    assert len(calls) == 1
    assert calls[0]["stdin"] == long_task
    assert "-" in calls[0]["raw_argv"]
    assert long_task not in calls[0]["raw_argv"]

    # 3. Agy (does not support plain stdin with output-format json) -> clear BAD_REQUEST
    conn = ConnectionView(auth_ref="cli:test", id="agy-large", provider_id="gemini", meta={"cli_path": str(bin_path)})
    req = ExecRequest(request_id=uuid4(), capability="ask_ai", params={"task": long_task}, connection=conn)
    res = await AgyCliExecutor().execute(req)
    assert not res.ok
    assert res.error_kind == ErrorKind.BAD_REQUEST
    assert "exceeds command-line length limit" in str(res.error)

    # 4. Hermes (argv-only oneshot) -> clear BAD_REQUEST
    conn = ConnectionView(auth_ref="cli:test", id="hermes-large", provider_id="hermes", meta={"cli_path": str(bin_path)})
    req = ExecRequest(request_id=uuid4(), capability="ask_ai", params={"task": long_task}, connection=conn)
    res = await HermesCliExecutor().execute(req)
    assert not res.ok
    assert res.error_kind == ErrorKind.BAD_REQUEST
    assert "exceeds command-line length limit" in str(res.error)


def test_shim_resolution() -> None:
    """resolve_shim resolves python and npm shims and detects unresolvable batch files."""
    with tempfile.TemporaryDirectory() as td:
        dir_path = Path(td)

        # 1. Python shim
        py_shim = dir_path / "test_py.cmd"
        runner_py = dir_path / "runner.py"
        runner_py.write_text("print('hi')", encoding="utf-8")
        py_shim.write_text(f'@"{sys.executable}" "{runner_py}" %*\n', encoding="utf-8")

        resolved, is_bin = resolve_shim(str(py_shim))
        assert is_bin
        assert resolved[0] == sys.executable
        assert resolved[1] == str(runner_py)

        # 2. Unresolvable batch file
        bad_bat = dir_path / "bad.bat"
        bad_bat.write_text("@echo off\necho hello\n", encoding="utf-8")
        resolved, is_bin = resolve_shim(str(bad_bat))
        assert not is_bin


@pytest.mark.asyncio
async def test_unresolvable_batch_shim_refused() -> None:
    """An unresolvable batch file (.bat/.cmd) is refused with error code / BAD_REQUEST."""
    with tempfile.TemporaryDirectory() as td:
        bad_bat = Path(td) / "unresolvable.bat"
        bad_bat.write_text("@echo off\necho arbitrary\n", encoding="utf-8")

        out = await run_cli_process([str(bad_bat), "arg1"])
        assert out.exit_code != 0
        assert "Refusing to execute unresolvable batch file" in out.stderr


@pytest.mark.asyncio
async def test_argv_flag_injection_rejected_all_drivers(fake_harness: FakeCliHarness) -> None:
    """session_id="--dangerously-bypass-approvals-and-sandbox" and model="-c x=y" rejected for all drivers."""
    drivers = [
        ("claude", ClaudeCliExecutor()),
        ("codex", CodexCliExecutor()),
        ("agy", AgyCliExecutor()),
        ("hermes", HermesCliExecutor()),
    ]

    for name, executor in drivers:
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli(name)

        conn = ConnectionView(
            auth_ref="cli:test",
            id=f"{name}-test",
            provider_id=name,
            meta={"cli_path": str(bin_path)},
        )

        # 1. Flag injection via session_id
        req_bad_session = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={
                "task": "hello",
                "session_id": "--dangerously-bypass-approvals-and-sandbox",
            },
            connection=conn,
        )
        res_session = await executor.execute(req_bad_session)
        assert not res_session.ok, f"{name} accepted injected session_id flag!"
        assert res_session.error_kind == ErrorKind.BAD_REQUEST
        assert len(fake_harness.get_calls()) == 0, f"{name} spawned a process for bad session_id!"

        # 2. Flag injection via model
        req_bad_model = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={
                "task": "hello",
                "model": "-c x=y",
            },
            connection=conn,
        )
        res_model = await executor.execute(req_bad_model)
        assert not res_model.ok, f"{name} accepted injected model flag!"
        assert res_model.error_kind == ErrorKind.BAD_REQUEST
        assert len(fake_harness.get_calls()) == 0, f"{name} spawned a process for bad model!"


@pytest.mark.asyncio
async def test_codex_inserts_double_dash_before_resume_session_id(fake_harness: FakeCliHarness) -> None:
    """Codex must insert '--' before positional session_id after 'resume'."""
    bin_path = fake_harness.register_cli("codex")
    fake_harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "ok"}}))

    conn = ConnectionView(
        auth_ref="cli:test",
        id="codex-test",
        provider_id="codex",
        meta={"cli_path": str(bin_path)},
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={
            "task": "continue",
            "session_id": "thread-safe-123",
        },
        connection=conn,
    )
    res = await CodexCliExecutor().execute(req)
    assert res.ok
    calls = fake_harness.get_calls()
    assert len(calls) == 1
    raw_argv = calls[0]["raw_argv"]
    assert "resume" in raw_argv
    resume_idx = raw_argv.index("resume")
    assert raw_argv[resume_idx + 1] == "--"
    assert raw_argv[resume_idx + 2] == "thread-safe-123"


@pytest.mark.asyncio
async def test_argv_length_cap_20k_quotes(fake_harness: FakeCliHarness) -> None:
    """A 20k prompt of double quotes must be rejected with BAD_REQUEST by agy and hermes."""
    quote_prompt = '"' * 20000

    for name, executor in [("agy", AgyCliExecutor()), ("hermes", HermesCliExecutor())]:
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli(name)

        conn = ConnectionView(
            auth_ref="cli:test",
            id=f"{name}-quotes",
            provider_id=name,
            meta={"cli_path": str(bin_path)},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": quote_prompt},
            connection=conn,
        )

        res = await executor.execute(req)
        assert not res.ok, f"{name} should have rejected 20k quotes command line"
        assert res.error_kind == ErrorKind.BAD_REQUEST
        assert "exceeds command-line length limit" in str(res.error)
        assert len(fake_harness.get_calls()) == 0, f"{name} spawned a process for oversized cmdline!"

