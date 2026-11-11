"""Readiness must fail closed without taking liveness down or disclosing dependency errors."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from typer.testing import CliRunner

from farm.control import doctor as diagnostics
from farm.control import run as runtime
from farm.control.cli import app
from farm.db.pool import DbPool
from farm.tenancy import MILVOW_TENANT_ID, TokenIdentity


async def _unused(scope: Any, receive: Any, send: Any) -> None:
    raise AssertionError("readiness/liveness must not enter MCP")


async def test_readiness_requires_auth_and_liveness_survives_database_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(runtime, "verify_token", verify)
    gateway = runtime.FarmAuthMiddleware(_unused, None)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://farm") as client:
        assert (await client.get("/ready")).status_code == 401
        assert (await client.get("/health")).status_code == 200
        assert verify.await_count == 0
        unavailable = await client.get("/ready", headers={"Authorization": "Bearer token"})
        assert unavailable.status_code == 503
        assert unavailable.json() == {"status": "unavailable"}
        assert (await client.get("/health")).status_code == 200


async def test_readiness_reports_dependency_failure_and_does_not_expose_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = TokenIdentity(token_id=uuid4(), tenant_id=MILVOW_TENANT_ID, user_id=None,
                             name="test", scopes=("mcp",))
    monkeypatch.setattr(runtime, "verify_token", AsyncMock(return_value=identity))
    probe = AsyncMock(return_value={"database": True, "scheduler": False})
    gateway = runtime.FarmAuthMiddleware(_unused, None, readiness=probe)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://farm") as client:
        headers = {"Authorization": "Bearer token"}
        response = await client.get("/ready", headers=headers)
        assert response.status_code == 503
        assert response.json()["checks"] == {"database": True, "scheduler": False}
        probe.return_value = {"database": True, "scheduler": True}
        assert (await client.get("/ready", headers=headers)).status_code == 200
        probe.side_effect = RuntimeError("secret connection string")
        response = await client.get("/ready", headers=headers)
        assert response.status_code == 503
        assert "secret" not in response.text


async def test_readiness_checks_schema_on_the_given_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from farm.control.doctor import ALEMBIC_INI

    heads = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_heads()
    cursor = AsyncMock()
    cursor.fetchall.return_value = [(head,) for head in heads]
    conn = AsyncMock()
    conn.execute.return_value = cursor
    marker = object()

    @asynccontextmanager
    async def connection(pool: DbPool) -> Any:
        assert pool is marker
        yield conn

    monkeypatch.setattr(runtime, "system_connection", connection)
    checks = await runtime.readiness_checks(marker, {"consumer": True})  # type: ignore[arg-type]
    assert checks == {"consumer": True, "database": True, "schema": True}
    cursor.fetchall.return_value = [("old_revision",)]
    checks = await runtime.readiness_checks(marker, {"consumer": True})  # type: ignore[arg-type]
    assert checks["database"] and not checks["schema"]
    conn.execute.side_effect = RuntimeError("secret")
    checks = await runtime.readiness_checks(marker, {"consumer": True})  # type: ignore[arg-type]
    assert not checks["database"] and not checks["schema"]


@pytest.mark.parametrize("status,code", [("PASS", 0), ("WARN", 0), ("FAIL", 1)])
def test_doctor_cli_is_registered_and_preserves_failure_exit_code(
    monkeypatch: pytest.MonkeyPatch, status: Any, code: int,
) -> None:
    monkeypatch.setattr(
        diagnostics, "run_doctor", AsyncMock(return_value=[diagnostics.CheckResult("database", status, "checked")])
    )
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == code, result.output
    assert "database: checked" in result.output
