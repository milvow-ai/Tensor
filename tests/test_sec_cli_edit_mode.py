"""Security tests for edit-mode confinement (Requirement 4).

mode=edit is permitted ONLY when:
1. connection meta.allow_edit is True (owner opt-in)
2. cwd resolves inside an allowed root (meta.edit_roots or default FARM_DATA_DIR/workspaces)
3. cwd already exists (never mkdir a caller path)
Violations must return BAD_REQUEST with no process execution.
Permission-skip flags applied ONLY in edit mode; answer mode uses read-only settings.
"""

import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from farm.executors.cli_agent.codex import CodexCliExecutor
from farm.executors.cli_agent.hermes import HermesCliExecutor
from farm.registry.models import ConnectionSpec
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness() -> FakeCliHarness:
    h = FakeCliHarness()
    yield h
    h.cleanup()


@pytest.mark.asyncio
async def test_edit_mode_rejected_when_allow_edit_is_not_set(fake_harness: FakeCliHarness) -> None:
    """mode=edit must be rejected with BAD_REQUEST if meta.allow_edit is missing or False."""
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
            auth_ref=f"cli:{name}-prod",
            id=f"{name}-prod",
            provider_id=name,
            meta={"cli_path": str(bin_path)},  # allow_edit is omitted
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "modify code", "mode": "edit", "cwd": "D:/some/path"},
            connection=conn,
        )

        res = await executor.execute(req)
        assert not res.ok, f"{name} should have rejected edit mode without allow_edit"
        assert res.error_kind == ErrorKind.BAD_REQUEST
        assert "allow_edit is false" in str(res.error) or "not permitted" in str(res.error)

        # No process should have been executed
        assert len(fake_harness.get_calls()) == 0


@pytest.mark.asyncio
async def test_edit_mode_rejected_when_allow_edit_explicitly_false(fake_harness: FakeCliHarness) -> None:
    """mode=edit must be rejected with BAD_REQUEST if meta.allow_edit is explicitly False."""
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
            auth_ref=f"cli:{name}-prod",
            id=f"{name}-prod",
            provider_id=name,
            meta={"cli_path": str(bin_path), "allow_edit": False},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "modify code", "mode": "edit", "cwd": "D:/some/path"},
            connection=conn,
        )

        res = await executor.execute(req)
        assert not res.ok
        assert res.error_kind == ErrorKind.BAD_REQUEST
        assert "meta.allow_edit is false" in str(res.error)
        assert len(fake_harness.get_calls()) == 0


@pytest.mark.asyncio
async def test_edit_mode_rejected_when_cwd_does_not_exist(fake_harness: FakeCliHarness) -> None:
    """mode=edit must fail if cwd does not exist; it must NEVER mkdir a non-existent caller path."""
    with tempfile.TemporaryDirectory() as td:
        root_dir = Path(td) / "workspaces"
        root_dir.mkdir()
        non_existent_cwd = root_dir / "does_not_exist_yet"

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
                auth_ref=f"cli:{name}-edit",
                id=f"{name}-edit",
                provider_id=name,
                meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [str(root_dir)]},
            )
            req = ExecRequest(
                request_id=uuid4(),
                capability="ask_ai",
                params={"task": "modify code", "mode": "edit", "cwd": str(non_existent_cwd)},
                connection=conn,
            )

            res = await executor.execute(req)
            assert not res.ok
            assert res.error_kind == ErrorKind.BAD_REQUEST
            assert "Working directory does not exist" in str(res.error)

            # Assert the directory was NOT created
            assert not non_existent_cwd.exists(), "Caller path was unexpectedly created via mkdir!"
            assert len(fake_harness.get_calls()) == 0


@pytest.mark.asyncio
async def test_edit_mode_rejected_when_cwd_outside_allowed_roots(fake_harness: FakeCliHarness) -> None:
    """mode=edit must reject cwd outside meta.edit_roots (e.g. path traversal or system dir)."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        allowed_root = td_path / "allowed_ws"
        allowed_root.mkdir()

        outside_dir = td_path / "outside_secret_dir"
        outside_dir.mkdir()

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
                auth_ref=f"cli:{name}-edit",
                id=f"{name}-edit",
                provider_id=name,
                meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [str(allowed_root)]},
            )
            req = ExecRequest(
                request_id=uuid4(),
                capability="ask_ai",
                params={"task": "modify code", "mode": "edit", "cwd": str(outside_dir)},
                connection=conn,
            )

            res = await executor.execute(req)
            assert not res.ok
            assert res.error_kind == ErrorKind.BAD_REQUEST
            assert "outside allowed edit roots" in str(res.error)
            assert len(fake_harness.get_calls()) == 0


@pytest.mark.asyncio
async def test_edit_mode_succeeds_with_valid_confinement(fake_harness: FakeCliHarness) -> None:
    """When allow_edit is True and cwd is valid inside allowed root, edit mode flags are passed."""
    with tempfile.TemporaryDirectory() as td:
        root_dir = Path(td) / "workspaces"
        root_dir.mkdir()
        valid_cwd = root_dir / "project-alpha"
        valid_cwd.mkdir()

        # 1. Claude: --permission-mode acceptEdits
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli("claude")
        fake_harness.set_response(stdout=json.dumps({"result": "edited", "is_error": False}))
        conn = ConnectionView(
            auth_ref="cli:claude-01",
            id="claude-01",
            provider_id="claude",
            meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [str(root_dir)]},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "edit files", "mode": "edit", "cwd": str(valid_cwd)},
            connection=conn,
        )
        res = await ClaudeCliExecutor().execute(req)
        assert res.ok
        calls = fake_harness.get_calls()
        assert "--permission-mode" in calls[0]["raw_argv"]
        assert "acceptEdits" in calls[0]["raw_argv"]
        assert "--allowedTools" not in calls[0]["raw_argv"]

        # 2. Codex: --sandbox workspace-write
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli("codex")
        fake_harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "edited"}}))
        conn = ConnectionView(
            auth_ref="cli:codex-01",
            id="codex-01",
            provider_id="codex",
            meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [str(root_dir)]},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "edit files", "mode": "edit", "cwd": str(valid_cwd)},
            connection=conn,
        )
        res = await CodexCliExecutor().execute(req)
        assert res.ok
        calls = fake_harness.get_calls()
        assert "--sandbox" in calls[0]["raw_argv"]
        assert "workspace-write" in calls[0]["raw_argv"]

        # 3. Agy: --mode accept-edits --dangerously-skip-permissions
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli("agy")
        fake_harness.set_response(stdout=json.dumps({"status": "SUCCESS", "response": "edited"}))
        conn = ConnectionView(
            auth_ref="cli:agy-01",
            id="agy-01",
            provider_id="gemini",
            meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [str(root_dir)]},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "edit files", "mode": "edit", "cwd": str(valid_cwd)},
            connection=conn,
        )
        res = await AgyCliExecutor().execute(req)
        assert res.ok
        calls = fake_harness.get_calls()
        assert "--mode" in calls[0]["raw_argv"]
        assert "accept-edits" in calls[0]["raw_argv"]
        assert "--dangerously-skip-permissions" in calls[0]["raw_argv"]


@pytest.mark.asyncio
async def test_answer_mode_uses_readonly_flags(fake_harness: FakeCliHarness) -> None:
    """mode=answer must enforce read-only / restricted tools even if allow_edit is set."""
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        ws.mkdir()

        # Claude: --allowedTools ""
        bin_path = fake_harness.register_cli("claude")
        fake_harness.set_response(stdout=json.dumps({"result": "answered", "is_error": False}))
        conn = ConnectionView(
            auth_ref="cli:claude-01",
            id="claude-01",
            provider_id="claude",
            meta={"cli_path": str(bin_path), "allow_edit": True},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "answer question", "mode": "answer", "cwd": str(ws)},
            connection=conn,
        )
        res = await ClaudeCliExecutor().execute(req)
        assert res.ok
        calls = fake_harness.get_calls()
        assert "--allowedTools" in calls[0]["raw_argv"]
        assert "--permission-mode" not in calls[0]["raw_argv"]

        # Codex: --sandbox read-only
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli("codex")
        fake_harness.set_response(stdout=json.dumps({"type": "item.completed", "item": {"text": "answered"}}))
        conn = ConnectionView(
            auth_ref="cli:codex-01",
            id="codex-01",
            provider_id="codex",
            meta={"cli_path": str(bin_path), "allow_edit": True},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "answer question", "mode": "answer", "cwd": str(ws)},
            connection=conn,
        )
        res = await CodexCliExecutor().execute(req)
        assert res.ok
        calls = fake_harness.get_calls()
        assert "--sandbox" in calls[0]["raw_argv"]
        assert "read-only" in calls[0]["raw_argv"]
        assert "workspace-write" not in calls[0]["raw_argv"]

        # Agy: --mode plan
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli("agy")
        fake_harness.set_response(stdout=json.dumps({"status": "SUCCESS", "response": "answered"}))
        conn = ConnectionView(
            auth_ref="cli:agy-01",
            id="agy-01",
            provider_id="gemini",
            meta={"cli_path": str(bin_path), "allow_edit": True},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "answer question", "mode": "answer", "cwd": str(ws)},
            connection=conn,
        )
        res = await AgyCliExecutor().execute(req)
        assert res.ok
        calls = fake_harness.get_calls()
        assert "--mode" in calls[0]["raw_argv"]
        assert "plan" in calls[0]["raw_argv"]
        assert "accept-edits" not in calls[0]["raw_argv"]

        # Hermes: --toolsets ""
        fake_harness.clear_calls()
        bin_path = fake_harness.register_cli("hermes")
        fake_harness.set_response(stdout="answered", usage_file_data={"estimated_cost_usd": 0.001})
        conn = ConnectionView(
            auth_ref="cli:hermes-01",
            id="hermes-01",
            provider_id="hermes",
            meta={"cli_path": str(bin_path), "allow_edit": True},
        )
        req = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "answer question", "mode": "answer", "cwd": str(ws)},
            connection=conn,
        )
        res = await HermesCliExecutor().execute(req)
        assert res.ok
        calls = fake_harness.get_calls()
        assert "--toolsets" in calls[0]["raw_argv"]


@pytest.mark.asyncio
async def test_cli_test_auth_ref_cannot_bypass_allow_edit(fake_harness: FakeCliHarness) -> None:
    """Production test hook removed: auth_ref='cli:test' cannot enter edit mode without allow_edit."""
    bin_path = fake_harness.register_cli("codex")

    conn = ConnectionView(
        auth_ref="cli:test",
        id="codex-test-hook",
        provider_id="codex",
        meta={"cli_path": str(bin_path)},  # allow_edit is omitted
    )
    req = ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={
            "task": "modify code",
            "mode": "edit",
            "session_id": "sess-test-hook",
        },
        connection=conn,
    )
    res = await CodexCliExecutor().execute(req)
    assert not res.ok
    assert res.error_kind == ErrorKind.BAD_REQUEST
    assert "allow_edit is false" in str(res.error)
    assert len(fake_harness.get_calls()) == 0


@pytest.mark.asyncio
async def test_allow_edit_string_false_strictness(fake_harness: FakeCliHarness) -> None:
    """allow_edit='false' string must fail validation or be rejected in edit mode; answer mode allowed."""
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        ws.mkdir()

        # 1. ConnectionSpec model validation rejects string "false"
        with pytest.raises(ValidationError):
            ConnectionSpec(
                id="test-conn-strict",
                auth_ref="cli:test",
                meta={"allow_edit": "false"},
            )

        # 2. Direct ConnectionView with "false" string rejected in edit mode
        bin_path = fake_harness.register_cli("claude")
        fake_harness.set_response(stdout=json.dumps({"result": "done", "is_error": False}))

        conn = ConnectionView(
            auth_ref="cli:test",
            id="test-conn-strict",
            provider_id="claude",
            meta={"cli_path": str(bin_path), "allow_edit": "false", "edit_roots": [str(ws)]},
        )
        req_edit = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "edit", "mode": "edit", "cwd": str(ws)},
            connection=conn,
        )
        res_edit = await ClaudeCliExecutor().execute(req_edit)
        assert not res_edit.ok
        assert res_edit.error_kind == ErrorKind.BAD_REQUEST
        assert "allow_edit is false" in str(res_edit.error)
        assert len(fake_harness.get_calls()) == 0

        # 3. Answer mode succeeds (answer mode does not require allow_edit)
        req_answer = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "answer", "mode": "answer", "cwd": str(ws)},
            connection=conn,
        )
        res_answer = await ClaudeCliExecutor().execute(req_answer)
        assert res_answer.ok


@pytest.mark.asyncio
async def test_edit_roots_empty_and_dot_rejected(fake_harness: FakeCliHarness) -> None:
    """edit_roots entries with '', '.', or relative paths must be rejected."""
    with tempfile.TemporaryDirectory() as td:
        ws = Path(td) / "ws"
        ws.mkdir()
        bin_path = fake_harness.register_cli("claude")

        # 1. ConnectionSpec rejects empty, '.', and relative roots
        for bad_root in ("", ".", "relative/dir"):
            with pytest.raises(ValidationError):
                ConnectionSpec(
                    id="test-conn-bad-roots",
                    auth_ref="cli:test",
                    meta={"allow_edit": True, "edit_roots": [bad_root]},
                )

        # 2. Executor validation rejects empty, '.', and relative roots in edit mode
        for bad_root in ("", ".", "relative/dir"):
            conn = ConnectionView(
                auth_ref="cli:test",
                id="test-conn-bad-roots",
                provider_id="claude",
                meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [bad_root]},
            )
            req = ExecRequest(
                request_id=uuid4(),
                capability="ask_ai",
                params={"task": "edit", "mode": "edit", "cwd": str(ws)},
                connection=conn,
            )
            res = await ClaudeCliExecutor().execute(req)
            assert not res.ok
            assert res.error_kind == ErrorKind.BAD_REQUEST
            assert "Invalid edit_roots entry" in str(res.error)
            assert len(fake_harness.get_calls()) == 0


@pytest.mark.asyncio
async def test_edit_root_traversal_prefix_case_and_dotdot(fake_harness: FakeCliHarness) -> None:
    """Uncovered edit-root traversals: prefix mismatch, .. escape, and case variation."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td).resolve()
        root = td_path / "root"
        root.mkdir()
        root2 = td_path / "root2"
        root2.mkdir()
        outside = td_path / "outside"
        outside.mkdir()
        sub = root / "SubDir"
        sub.mkdir()

        bin_path = fake_harness.register_cli("claude")
        fake_harness.set_response(stdout=json.dumps({"result": "done", "is_error": False}))

        conn = ConnectionView(
            auth_ref="cli:test",
            id="test-conn-traversal",
            provider_id="claude",
            meta={"cli_path": str(bin_path), "allow_edit": True, "edit_roots": [str(root)]},
        )

        # 1. Prefix mismatch: D:\root vs D:\root2 must be rejected
        req_prefix = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "edit", "mode": "edit", "cwd": str(root2)},
            connection=conn,
        )
        res_prefix = await ClaudeCliExecutor().execute(req_prefix)
        assert not res_prefix.ok
        assert res_prefix.error_kind == ErrorKind.BAD_REQUEST
        assert "outside allowed edit roots" in str(res_prefix.error)

        # 2. Path traversal with ..: root/../outside must be rejected
        dotdot_path = root / ".." / "outside"
        req_dotdot = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "edit", "mode": "edit", "cwd": str(dotdot_path)},
            connection=conn,
        )
        res_dotdot = await ClaudeCliExecutor().execute(req_dotdot)
        assert not res_dotdot.ok
        assert res_dotdot.error_kind == ErrorKind.BAD_REQUEST
        assert "outside allowed edit roots" in str(res_dotdot.error)

        # 3. Valid subfolder within root succeeds
        req_valid = ExecRequest(
            request_id=uuid4(),
            capability="ask_ai",
            params={"task": "edit", "mode": "edit", "cwd": str(sub)},
            connection=conn,
        )
        res_valid = await ClaudeCliExecutor().execute(req_valid)
        assert res_valid.ok

