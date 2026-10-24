"""The real Codex and Claude sign-in drivers, end to end through the LoginService, against fake CLIs."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from farm.connect import sessions
from farm.connect.drivers.claude import ClaudePasteDriver
from farm.connect.drivers.codex import CodexDeviceDriver
from farm.connect.drivers.registry import driver_for
from farm.connect.sealed import load_or_create_keys, seal
from farm.connect.service import CheckResult, LoginService
from farm.db.pool import DbPool, tenant_connection
from farm.tenancy import MILVOW_TENANT_ID
from tests.conftest import seed_connection

FAKES = Path(__file__).parent / "fakes"


def _cli(name: str) -> list[str]:
    return [sys.executable, str(FAKES / f"fake_{name}.py")]


async def _never(connection_id: str, home: Path) -> CheckResult:
    raise AssertionError("the drivers test themselves")


def _service(pool: DbPool, tmp_path: Path, make: Any) -> LoginService:
    return LoginService(pool, driver_factory=make, test_job=_never, keys_dir=tmp_path / "keys", sweep_interval_s=3600)


async def _account(pool: DbPool, provider: str, conn_id: str, home: Path, **meta: Any) -> None:
    await seed_connection(
        pool, provider, conn_id, kind="ai", executor="cli_agent", status="needs_login", auth_ref=f"cli:{conn_id}",
        meta={"cli": provider, "home" if provider == "codex" else "config_dir": str(home), **meta},
    )


async def _wait(pool: DbPool, session_id: UUID, *states: str, timeout: float = 20.0) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    row: dict[str, Any] | None = None
    while loop.time() < deadline:
        row = await sessions.get(pool, session_id)
        if row is not None and row["state"] in states:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"session stayed {row and row['state']} ({row and row['error_excerpt']}), wanted {states}")


async def _status(pool: DbPool, conn_id: str) -> tuple[str, dict[str, Any]]:
    async with tenant_connection(pool) as conn:
        cur = await conn.execute("select status, meta from public.connections where id = %s", (conn_id,))
        row = await cur.fetchone()
    assert row is not None
    return row[0], row[1]


async def test_codex_device_code_signs_a_new_account_in(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_DELAY", "1.5")
    home = tmp_path / "ai" / "codex-05"
    await _account(pool, "codex", "codex-05", home)
    svc = _service(pool, tmp_path, lambda p, c, m: CodexDeviceDriver(c, m, command=_cli("codex")))
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "codex-05", "owner")
        row = await _wait(pool, sid, "waiting_for_user")
        assert row["user_code"] == "F91M-9W7OM" and row["verification_uri"] == "https://auth.openai.com/codex/device"
        row = await _wait(pool, sid, "done")
    finally:
        await svc.stop()
    assert row["identity"]["plan"] == "plus" and row["identity"]["email_masked"].startswith("j")
    assert (home / "auth.json").exists()
    assert 'cli_auth_credentials_store = "file"' in (home / "config.toml").read_text(encoding="utf-8")
    status, meta = await _status(pool, "codex-05")
    assert status == "active" and meta["login"]["method"] == "device_code"


async def test_codex_relogin_replaces_only_the_login(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_DELAY", "0.2")
    home = tmp_path / "ai" / "codex-02"
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / "rollout.jsonl").write_text("history", encoding="utf-8")
    (home / "config.toml").write_text('model = "gpt-5"\n', encoding="utf-8")
    (home / "auth.json").write_text('{"tokens": {}}', encoding="utf-8")
    await _account(pool, "codex", "codex-02", home)
    svc = _service(pool, tmp_path, lambda p, c, m: CodexDeviceDriver(c, m, command=_cli("codex")))
    await svc.start()
    try:
        row = await _wait(pool, await svc.start_login(MILVOW_TENANT_ID, "codex-02", "owner"), "done")
    finally:
        await svc.stop()
    assert (home / "sessions" / "rollout.jsonl").read_text(encoding="utf-8") == "history"
    config = (home / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5"' in config and "cli_auth_credentials_store" in config
    assert "id_token" in (home / "auth.json").read_text(encoding="utf-8")
    assert (home / "auth.json.prev").read_text(encoding="utf-8") == '{"tokens": {}}'
    assert not list(home.parent.glob("codex-02.login-*")) and row["evidence"]["test_ok"] is True


async def test_an_unapproved_device_code_ends_expired(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CODEX_MODE", "timeout")
    monkeypatch.setenv("FAKE_CODEX_DELAY", "0.2")
    await _account(pool, "codex", "codex-05", tmp_path / "ai" / "codex-05")
    svc = _service(pool, tmp_path, lambda p, c, m: CodexDeviceDriver(c, m, command=_cli("codex")))
    await svc.start()
    try:
        row = await _wait(pool, await svc.start_login(MILVOW_TENANT_ID, "codex-05", "owner"), "failed")
    finally:
        await svc.stop()
    assert row["error_kind"] == "expired" and "timed out" in row["error_excerpt"]


@pytest.mark.parametrize("existing", [False, True], ids=["new-account", "relogin"])
async def test_claude_pasted_code_signs_in(pool: DbPool, tmp_path: Path, existing: bool) -> None:
    home = tmp_path / "ai" / "claude-05"
    if existing:
        home.mkdir(parents=True)
        (home / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
        (home / ".claude.json").write_text(json.dumps({"projects": {"x": 1}, "oauthAccount": {"emailAddress": "old"}}),
                                           encoding="utf-8")
    await _account(pool, "claude", "claude-05", home, expected_email="jane@example.com")
    svc = _service(pool, tmp_path, lambda p, c, m: ClaudePasteDriver(c, m, command=_cli("claude")))
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, "waiting_for_user")
        while not row["needs_input"]:
            await asyncio.sleep(0.05)
            row = await _wait(pool, sid, "waiting_for_user")
        assert "oauth/authorize" in row["auth_url"] and row["method"] == "paste_code"
        jwk = load_or_create_keys(tmp_path / "keys")[1]
        await svc.submit_input(MILVOW_TENANT_ID, sid, seal("good-code", jwk, f"{MILVOW_TENANT_ID}|{sid}|login_input"), "o")
        row = await _wait(pool, sid, "done")
    finally:
        await svc.stop()
    assert row["identity"]["plan"] == "pro" and row["identity"]["org"] == "Jane's Org"
    assert row["evidence"]["models"] == ["opus", "sonnet", "haiku"]
    assert (home / ".credentials.json").exists()
    account = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert account["oauthAccount"]["emailAddress"] == "jane@example.com"
    if existing:
        assert account["projects"] == {"x": 1}
        assert (home / "settings.json").read_text(encoding="utf-8") == '{"theme": "dark"}'
    status, _ = await _status(pool, "claude-05")
    assert status == "active"


async def test_a_refused_claude_code_fails_and_keeps_the_account(pool: DbPool, tmp_path: Path) -> None:
    home = tmp_path / "ai" / "claude-05"
    await _account(pool, "claude", "claude-05", home)
    svc = _service(pool, tmp_path, lambda p, c, m: ClaudePasteDriver(c, m, command=_cli("claude")))
    await svc.start()
    try:
        sid = await svc.start_login(MILVOW_TENANT_ID, "claude-05", "owner")
        row = await _wait(pool, sid, "waiting_for_user")
        while not row["needs_input"]:
            await asyncio.sleep(0.05)
            row = await _wait(pool, sid, "waiting_for_user")
        jwk = load_or_create_keys(tmp_path / "keys")[1]
        await svc.submit_input(MILVOW_TENANT_ID, sid, seal("wrong", jwk, f"{MILVOW_TENANT_ID}|{sid}|login_input"), "o")
        row = await _wait(pool, sid, "failed")
    finally:
        await svc.stop()
    assert row["error_kind"] == "code_refused" and "400" in row["error_excerpt"]
    assert not (home / ".credentials.json").exists()
    status, _ = await _status(pool, "claude-05")
    assert status == "needs_login"


def test_driver_for_picks_by_cli() -> None:
    assert isinstance(driver_for("claude", "claude-05", {"cli": "claude"}), ClaudePasteDriver)
    assert isinstance(driver_for("codex", "codex-05", {"cli": "codex"}), CodexDeviceDriver)
    with pytest.raises(NotImplementedError):
        driver_for("gemini", "agy-08", {"cli": "agy"})
    with pytest.raises(NotImplementedError, match="WSL"):
        driver_for("codex", "codex-01", {"cli": "codex", "cli_prefix": ["wsl.exe"]})
