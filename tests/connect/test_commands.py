"""Sign-in commands through the Farm's command path (``execute_command`` and the queued ``process_command``)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from farm.connect import sessions
from farm.connect.sealed import load_or_create_keys, seal
from farm.connect.service import CheckResult, LoginService, set_login_service
from farm.control.commands import execute_command, process_command
from farm.db.pool import DbPool, tenant_connection
from farm.tenancy import MILVOW_TENANT_ID
from tests.conftest import seed_connection


def _no_driver(provider_id: str, connection_id: str, meta: Any) -> Any:
    raise NotImplementedError("no driver in this test")


async def _ok(connection_id: str, home: Path) -> CheckResult:
    return CheckResult(ok=True, run_id="run-1")


@pytest.fixture
def service(pool: DbPool, tmp_path: Path) -> Iterator[LoginService]:
    svc = LoginService(pool, driver_factory=_no_driver, test_job=_ok, keys_dir=tmp_path / "keys")
    set_login_service(svc)
    yield svc
    set_login_service(None)


def _jwk(tmp_path: Path) -> dict[str, Any]:
    return dict(load_or_create_keys(tmp_path / "keys")[1])


async def _ai(pool: DbPool, provider: str, conn_id: str, **meta: Any) -> None:
    await seed_connection(
        pool, provider, conn_id, kind="ai", executor="cli_agent", status="active", auth_ref=f"cli:{conn_id}",
        meta=meta,
    )


async def _rows(pool: DbPool, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(sql, args)
        return list(await cur.fetchall())


async def test_add_accounts_numbers_after_the_pool_and_opens_a_session_each(
    pool: DbPool, service: LoginService
) -> None:
    for n in (1, 2, 4):
        await _ai(pool, "claude", f"claude-0{n}", cli="claude", models=["opus", "sonnet"], timeout_s=900)
    status, result = await execute_command(
        pool,
        uuid4(),
        "add_accounts",
        {"provider_id": "claude", "count": 2, "plan": "pro", "emails": ["jane@example.com"]},
        actor="owner",
    )
    assert status == "done", result
    assert result["connection_ids"] == ["claude-05", "claude-06"] and len(result["session_ids"]) == 2
    first, second = await _rows(
        pool,
        "select label, status, meta, plan from public.connections where id = any(%s) order by id",
        ["claude-05", "claude-06"],
    )
    assert first[0] == "Claude (jane@example.com)" and second[0] == "Claude (claude-06)"
    assert first[1] == "needs_login" and first[2]["expected_email"] == "jane@example.com"
    assert first[2]["cli"] == "claude" and first[2]["models"] == ["opus", "sonnet"] and first[2]["timeout_s"] == 900
    assert first[3] == {"name": "pro"} and "expected_email" not in second[2]
    for session_id in result["session_ids"]:
        row = await sessions.get(pool, UUID(session_id))
        assert row is not None and row["error_kind"] == "not_implemented"  # this test has no driver


@pytest.mark.parametrize(("cli", "expected"), [(None, "agy-08"), ("gemini", "gemini-cli-03")])
async def test_add_accounts_for_gemini_follows_its_cli(
    pool: DbPool, service: LoginService, cli: str | None, expected: str
) -> None:
    await _ai(pool, "gemini", "agy-01", cli="agy")
    await _ai(pool, "gemini", "agy-07", cli="agy")
    await _ai(pool, "gemini", "gemini-cli-02", cli="gemini")
    payload: dict[str, Any] = {"provider_id": "gemini", "count": 1}
    if cli:
        payload["cli"] = cli
    status, result = await execute_command(pool, uuid4(), "add_accounts", payload, actor="owner")
    assert status == "done", result
    assert result["connection_ids"] == [expected]


async def test_save_secret_writes_the_key_and_points_the_account_at_it(
    pool: DbPool, service: LoginService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved: dict[str, str] = {}

    def fake_set_secret(name: str, value: str) -> Path:
        saved[name] = value
        return tmp_path / ".env"

    monkeypatch.setattr("farm.connect.commands.set_secret", fake_set_secret)
    monkeypatch.setenv("REOON_KEY_01", "placeholder")  # restored (unset) after the test
    await seed_connection(pool, "reoon", "reoon-01", status="needs_login", auth_ref="env:REOON_OLD")
    sealed = seal("rk_live_123", _jwk(tmp_path), f"{MILVOW_TENANT_ID}|REOON_KEY_01|secret")
    status, result = await execute_command(
        pool,
        uuid4(),
        "save_secret",
        {"name": "REOON_KEY_01", "sealed": sealed, "connection_id": "reoon-01"},
        actor="owner",
    )
    assert (status, result) == ("done", {"saved": "REOON_KEY_01"})
    assert saved == {"REOON_KEY_01": "rk_live_123"} and os.environ["REOON_KEY_01"] == "rk_live_123"
    assert await _rows(pool, "select auth_ref from public.connections where id = 'reoon-01'") == [
        ("env:REOON_KEY_01",)
    ]
    assert await _rows(pool, "select action from public.audit_events where action = 'save_secret'") == [
        ("save_secret",)
    ]


async def test_a_key_sealed_for_another_name_is_rejected(
    pool: DbPool, service: LoginService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("farm.connect.commands.set_secret", lambda name, value: pytest.fail("must not save"))
    sealed = seal("rk_live_123", _jwk(tmp_path), f"{MILVOW_TENANT_ID}|OTHER_NAME|secret")
    status, result = await execute_command(
        pool, uuid4(), "save_secret", {"name": "REOON_KEY_01", "sealed": sealed}, actor="owner"
    )
    assert status == "rejected" and "rk_live_123" not in json.dumps(result)


@pytest.mark.parametrize("sealed_for", ["HUNTER_KEY_01", "WRONG_NAME"])
async def test_a_queued_sealed_command_keeps_no_ciphertext(
    pool: DbPool, service: LoginService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sealed_for: str
) -> None:
    monkeypatch.setattr("farm.connect.commands.set_secret", lambda name, value: tmp_path / ".env")
    monkeypatch.setenv("HUNTER_KEY_01", "placeholder")
    payload = {"name": "HUNTER_KEY_01", "sealed": seal("hk_1", _jwk(tmp_path), f"{MILVOW_TENANT_ID}|{sealed_for}|secret")}
    cmd_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'save_secret', %s::jsonb, 'queued', 'owner')",
            (cmd_id, json.dumps(payload)),
        )
    status, _ = await process_command(pool, cmd_id)
    assert status == ("done" if sealed_for == "HUNTER_KEY_01" else "rejected")
    [(stored,)] = await _rows(pool, "select payload from public.farm_commands where id = %s", cmd_id)
    assert stored == {"name": "HUNTER_KEY_01", "sealed": "consumed"}


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        pytest.param("save_secret", {"name": "K", "sealed": {"v": 1}, "value": "sk-live-abc"}, id="extra-field"),
        pytest.param("start_login", {"connection_id": "Bad ID"}, id="bad-id"),
        pytest.param("add_accounts", {"provider_id": "claude", "count": 11}, id="too-many"),
        pytest.param("add_accounts", {"provider_id": "claude", "count": 1, "emails": ["a@b.c", "d@e.f"]}, id="emails"),
        pytest.param("start_login", {"connection_id": "nope-01"}, id="unknown-account"),
    ],
)
async def test_bad_sign_in_commands_are_rejected(
    pool: DbPool, service: LoginService, kind: str, payload: dict[str, Any]
) -> None:
    status, result = await execute_command(pool, uuid4(), kind, payload, actor="owner")
    assert status == "rejected", result
    assert "sk-live-abc" not in json.dumps(result)


async def test_without_the_service_sign_in_commands_are_refused(pool: DbPool) -> None:
    await _ai(pool, "claude", "claude-01", cli="claude")
    set_login_service(None)
    status, result = await execute_command(pool, uuid4(), "start_login", {"connection_id": "claude-01"}, actor="o")
    assert status == "rejected" and "not running" in result["error"]
