"""Tests for farm connect CLI: snippet formats, env-var token references, verification status, and --write."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from farm.control.cli import app

runner = CliRunner()


def test_connect_claude_code_contains_env_var_and_verified() -> None:
    result = runner.invoke(app, ["connect", "claude-code"])
    assert result.exit_code == 0
    assert "[VERIFIED]" in result.stdout
    assert "http://127.0.0.1:8787/mcp" in result.stdout
    assert "${FARM_TOKEN}" in result.stdout
    assert "Authorization: Bearer ${FARM_TOKEN}" in result.stdout
    # Must never include a literal token
    assert "farm_tok_" not in result.stdout


def test_connect_codex_contains_bearer_env_var_and_verified() -> None:
    result = runner.invoke(app, ["connect", "codex"])
    assert result.exit_code == 0
    assert "[VERIFIED]" in result.stdout
    assert "http://127.0.0.1:8787/mcp" in result.stdout
    assert 'bearer_token_env_var = "FARM_TOKEN"' in result.stdout


def test_connect_cursor_marked_unverified() -> None:
    result = runner.invoke(app, ["connect", "cursor"])
    assert result.exit_code == 0
    assert "[UNVERIFIED]" in result.stdout
    assert "http://127.0.0.1:8787/mcp" in result.stdout
    assert "${FARM_TOKEN}" in result.stdout


def test_connect_antigravity_gemini_marked_unverified() -> None:
    for name in ("antigravity", "gemini"):
        result = runner.invoke(app, ["connect", name])
        assert result.exit_code == 0
        assert "[UNVERIFIED]" in result.stdout
        assert "http://127.0.0.1:8787/mcp" in result.stdout


def test_connect_generic_marked_unverified() -> None:
    result = runner.invoke(app, ["connect", "generic"])
    assert result.exit_code == 0
    assert "[UNVERIFIED]" in result.stdout
    assert "http://127.0.0.1:8787/mcp" in result.stdout


def test_connect_unknown_target_fails() -> None:
    result = runner.invoke(app, ["connect", "unsupported-ide"])
    assert result.exit_code != 0
    assert "unknown target" in result.stdout.lower() or "unknown target" in result.stderr.lower()


def test_connect_write_unverified_refused() -> None:
    result = runner.invoke(app, ["connect", "cursor", "--write"])
    assert result.exit_code != 0
    assert "unverified" in result.stdout.lower() or "unverified" in result.stderr.lower()


def test_connect_write_claude_code_with_confirmation(tmp_path: Path, monkeypatch: Any) -> None:
    config_file = tmp_path / ".claude.json"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # User confirms 'y'
    result = runner.invoke(app, ["connect", "claude-code", "--write"], input="y\n")
    assert result.exit_code == 0
    assert config_file.is_file()

    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    server_cfg = data["mcpServers"]["harness-farm"]
    assert server_cfg["url"] == "http://127.0.0.1:8787/mcp"
    assert server_cfg["headers"]["Authorization"] == "Bearer ${FARM_TOKEN}"


def test_connect_write_codex_with_confirmation(tmp_path: Path, monkeypatch: Any) -> None:
    config_dir = tmp_path / ".codex"
    config_file = config_dir / "config.toml"
    monkeypatch.setenv("CODEX_HOME", str(config_dir))

    result = runner.invoke(app, ["connect", "codex", "--write"], input="y\n")
    assert result.exit_code == 0
    assert config_file.is_file()

    content = config_file.read_text(encoding="utf-8")
    assert "[mcp_servers.harness-farm]" in content
    assert 'bearer_token_env_var = "FARM_TOKEN"' in content
