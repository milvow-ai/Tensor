"""Security tests for CLI agent environment isolation (Requirement 1).

Parent environment secrets must never leak into delegated child CLI processes.
OS essentials and driver config directories are preserved; NO_COLOR=1 is enforced.
"""

import json
import os
from uuid import uuid4

import pytest

from farm.executors.base import ConnectionView, ExecRequest
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.base import build_child_env
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from farm.executors.cli_agent.codex import CodexCliExecutor
from farm.executors.cli_agent.hermes import HermesCliExecutor
from tests.test_cli_agent_fakes import FakeCliHarness


@pytest.fixture
def fake_harness() -> FakeCliHarness:
    h = FakeCliHarness()
    yield h
    h.cleanup()


def test_build_child_env_filters_secrets() -> None:
    """build_child_env must only pass OS essentials and safe extra vars, never secrets."""
    secret_pw = "pass" + "word123"
    fake_db = f"postgres://farm_user:{secret_pw}" + "@" + "localhost:5432/farm"
    orig_env = dict(os.environ)

    try:
        os.environ["FARM_DB_URL"] = fake_db
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "supa-secret-key-1"
        os.environ["APOLLO_API_KEY"] = "apollo-secret-key-2"
        os.environ["BIFROST_FARM_VK"] = "bifrost-vk-secret-3"
        os.environ["OPENROUTER_API_KEY"] = "openrouter-secret-key-4"

        # Even if extra requests a secret variable name or value
        extra = {
            "CLAUDE_CONFIG_DIR": "D:/farm-data/ai/claude-01",
            "EXTRA_KEY": "should-be-blocked",
            "EXTRA_PASSWORD": "should-be-blocked-too",
            "LEAK_URL": fake_db,
        }

        child = build_child_env(extra)

        # Check sentinel assertions
        assert "FARM_DB_URL" not in child
        assert "SUPABASE_SERVICE_ROLE_KEY" not in child
        assert "APOLLO_API_KEY" not in child
        assert "BIFROST_FARM_VK" not in child
        assert "OPENROUTER_API_KEY" not in child
        assert "EXTRA_KEY" not in child
        assert "EXTRA_PASSWORD" not in child
        assert "LEAK_URL" not in child

        # Check values are absent
        for val in (fake_db, secret_pw, "supa-secret-key-1", "apollo-secret-key-2", "bifrost-vk-secret-3"):
            for child_val in child.values():
                assert val not in child_val

        # Driver var and essentials present
        assert child["CLAUDE_CONFIG_DIR"] == "D:/farm-data/ai/claude-01"
        assert child["NO_COLOR"] == "1"
        if "PATH" in orig_env:
            assert "PATH" in child
    finally:
        os.environ.clear()
        os.environ.update(orig_env)


@pytest.mark.asyncio
async def test_all_four_drivers_env_isolation(fake_harness: FakeCliHarness) -> None:
    """Each of the four drivers (Claude, Codex, Agy, Hermes) runs and child env is verified clean."""
    secret_pw = "pass" + "word456"
    fake_db = f"postgres://farm_admin:{secret_pw}" + "@" + "127.0.0.1:5432/farm_db"
    orig_env = dict(os.environ)

    try:
        os.environ["FARM_DB_URL"] = fake_db
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "supa-serv-role-val"
        os.environ["APOLLO_API_KEY"] = "apollo-val-987"
        os.environ["BIFROST_FARM_VK"] = "bifrost-vk-val-123"
        os.environ["OPENROUTER_API_KEY"] = "openrouter-val-555"

        drivers = [
            ("claude", ClaudeCliExecutor(), "CLAUDE_CONFIG_DIR", "D:/farm-data/ai/claude-test"),
            ("codex", CodexCliExecutor(), "CODEX_HOME", "D:/farm-data/ai/codex-test"),
            ("agy", AgyCliExecutor(), None, None),
            ("hermes", HermesCliExecutor(), "TERMINAL_CWD", None),
        ]

        for name, executor, expected_var, expected_val in drivers:
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

            meta: dict[str, str] = {"cli_path": str(bin_path)}
            if expected_var == "CLAUDE_CONFIG_DIR" and expected_val:
                meta["config_dir"] = expected_val
            elif expected_var == "CODEX_HOME" and expected_val:
                meta["config_dir"] = expected_val

            conn = ConnectionView(
                auth_ref="cli:test",
                id=f"{name}-test",
                provider_id=name,
                meta=meta,
            )
            req = ExecRequest(
                request_id=uuid4(),
                capability="ask_ai",
                params={"task": f"test {name}", "mode": "answer"},
                connection=conn,
            )

            res = await executor.execute(req)
            assert res.ok, f"Executor {name} failed: {res.error}"

            calls = fake_harness.get_calls()
            assert len(calls) == 1, f"Expected 1 call for {name}"
            child_env = calls[0].get("full_env", {})

            # Sentinel assertions: parent secrets must NEVER be present
            assert "FARM_DB_URL" not in child_env, f"FARM_DB_URL leaked to {name}"
            assert "SUPABASE_SERVICE_ROLE_KEY" not in child_env, f"SUPABASE_SERVICE_ROLE_KEY leaked to {name}"
            assert "APOLLO_API_KEY" not in child_env, f"APOLLO_API_KEY leaked to {name}"
            assert "BIFROST_FARM_VK" not in child_env, f"BIFROST_FARM_VK leaked to {name}"
            assert "OPENROUTER_API_KEY" not in child_env, f"OPENROUTER_API_KEY leaked to {name}"

            # Check that secret values are completely absent from child env values
            for leaked_val in (fake_db, secret_pw, "supa-serv-role-val", "apollo-val-987", "bifrost-vk-val-123"):
                for v in child_env.values():
                    assert leaked_val not in v, f"Secret value leaked into {name} env: {v}"

            # NO_COLOR=1 must always be set
            assert child_env.get("NO_COLOR") == "1", f"NO_COLOR not set for {name}"

            # Verify driver's expected config var is present
            if expected_var and expected_val:
                assert child_env.get(expected_var) == expected_val, f"{expected_var} missing for {name}"
            elif expected_var == "TERMINAL_CWD":
                assert "TERMINAL_CWD" in child_env, f"TERMINAL_CWD missing for {name}"

    finally:
        os.environ.clear()
        os.environ.update(orig_env)


def test_doctor_check_ai_clis_env_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doctor check_ai_clis must pass env=build_child_env(), containing no secret names."""
    from unittest.mock import MagicMock

    from farm.control.doctor import check_ai_clis

    orig_env = dict(os.environ)
    try:
        os.environ["FARM_DB_URL"] = "postgres://user:pass@localhost/db"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "super-secret-key"
        os.environ["OPENROUTER_API_KEY"] = "secret-token-val"
        os.environ["CUSTOM_PASSWORD"] = "secret-pass"
        os.environ["AGENT_VK"] = "vk-12345"

        passed_envs: list[dict[str, str]] = []

        def fake_run(argv: list[str], **kwargs: object) -> MagicMock:
            env = kwargs.get("env")
            if isinstance(env, dict):
                passed_envs.append(env)
            mock_res = MagicMock()
            mock_res.stdout = "claude 1.0.0"
            mock_res.stderr = ""
            return mock_res

        monkeypatch.setattr("shutil.which", lambda name: f"/fake/bin/{name}")
        monkeypatch.setattr("subprocess.run", fake_run)

        res = check_ai_clis(["claude"])
        assert res.status == "PASS"
        assert len(passed_envs) == 1

        child_env = passed_envs[0]
        # Assert no secret names leaked
        assert "FARM_DB_URL" not in child_env
        assert "SUPABASE_SERVICE_ROLE_KEY" not in child_env
        assert "OPENROUTER_API_KEY" not in child_env
        assert "CUSTOM_PASSWORD" not in child_env
        assert "AGENT_VK" not in child_env

        for k in child_env:
            k_upper = k.upper()
            assert "SECRET" not in k_upper
            assert "PASSWORD" not in k_upper
            assert "TOKEN" not in k_upper
            assert "KEY" not in k_upper
            assert "_VK" not in k_upper
    finally:
        os.environ.clear()
        os.environ.update(orig_env)

