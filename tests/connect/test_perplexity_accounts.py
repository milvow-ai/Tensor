"""Perplexity accounts: API-key AI accounts are added without a sign-in session and signed in by saving a key."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from farm.connect.service import CheckResult, LoginService, set_login_service
from farm.control.commands import execute_command
from farm.db.pool import DbPool, tenant_connection
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


async def _rows(pool: DbPool, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(sql, args)
        return list(await cur.fetchall())


async def test_add_accounts_creates_the_perplexity_pool_without_sign_in_sessions(
    pool: DbPool, service: LoginService
) -> None:
    status, result = await execute_command(
        pool, uuid4(), "add_accounts", {"provider_id": "perplexity", "count": 2}, actor="owner"
    )
    assert status == "done", result
    assert result["connection_ids"] == ["perplexity-01", "perplexity-02"] and result["session_ids"] == []
    rows = await _rows(
        pool,
        "select id, label, status, auth_ref, meta from public.connections where provider_id = 'perplexity' "
        "order by id",
    )
    assert [r[0] for r in rows] == ["perplexity-01", "perplexity-02"]
    for row in rows:
        assert row[1].startswith("Perplexity (") and row[2] == "needs_login"
        assert row[3] == f"cli:{row[0]}"  # no key yet: the executor and test_connection ask for one
        assert row[4]["cli"] == "perplexity" and row[4]["model"] == "sonar" and "sonar-pro" in row[4]["models"]
    assert await _rows(pool, "select kind, executor from public.providers where id = 'perplexity'") == [
        ("ai", "cli_agent")
    ]
    assert await _rows(pool, "select 1 from public.login_sessions") == []


async def test_start_login_for_a_perplexity_account_says_to_paste_a_key(
    pool: DbPool, service: LoginService
) -> None:
    await seed_connection(
        pool, "perplexity", "perplexity-01", kind="ai", executor="cli_agent", status="needs_login",
        auth_ref="cli:perplexity-01", meta={"cli": "perplexity"},
    )
    status, result = await execute_command(
        pool, uuid4(), "start_login", {"connection_id": "perplexity-01"}, actor="owner"
    )
    assert status == "rejected" and "API key" in result["error"]
    assert await _rows(pool, "select 1 from public.login_sessions") == []
