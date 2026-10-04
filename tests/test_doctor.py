"""Unit and component tests for farm doctor diagnostics suite."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import respx
from httpx import Response

from farm.control.doctor import (
    CheckResult,
    check_ai_clis,
    check_bifrost_health,
    check_circuits,
    check_data_dir_writable,
    check_db_and_migrations,
    check_disk_space,
    check_evidence_dir,
    check_exhausted_accounts,
    check_needs_login,
    check_provider_pools,
    doctor_exit_code,
    format_doctor_report,
    run_doctor,
)
from farm.db.pool import DbPool
from tests.conftest import seed_connection


def test_check_db_and_migrations_ok(db_url: str) -> None:
    """Migrated database returns PASS with current revision."""
    res = check_db_and_migrations(db_url=db_url)
    assert res.status == "PASS"
    assert "migrations at head" in res.detail


def test_check_db_and_migrations_unreachable() -> None:
    """Unreachable database returns FAIL with fix hint without crashing."""
    invalid_url = "postgresql://farm:test@127.0.0.1:1/nonexistent_db"
    res = check_db_and_migrations(db_url=invalid_url)
    assert res.status == "FAIL"
    assert "Database unreachable" in res.detail
    assert "farm db up" in res.fix_hint or "FARM_DB_URL" in res.fix_hint


@pytest.mark.asyncio
@respx.mock
async def test_check_bifrost_health_ok() -> None:
    """Healthy Bifrost responding HTTP 200 returns PASS."""
    respx.get("http://127.0.0.1:8080/health").mock(return_value=Response(200, json={"status": "ok"}))
    res = await check_bifrost_health("http://127.0.0.1:8080")
    assert res.status == "PASS"
    assert "HTTP 200" in res.detail


@pytest.mark.asyncio
async def test_check_bifrost_health_down() -> None:
    """Unreachable Bifrost port returns FAIL with fix hint."""
    res = await check_bifrost_health("http://127.0.0.1:1")
    assert res.status == "FAIL"
    assert "start-bifrost.ps1" in res.fix_hint


@pytest.mark.asyncio
async def test_check_provider_pools(pool: DbPool) -> None:
    """Validates that pools with active connections pass, and pools without connections warn."""
    # Seed active provider and connection
    await seed_connection(pool, "test_prov_a", "test_conn_a", status="active")
    res1 = await check_provider_pools(pool)
    assert res1.status == "PASS"
    assert "test_prov_a" in res1.detail

    # Seed enabled provider with no connections
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.providers (id, name, kind, executor, enabled) "
            "values ('test_prov_b', 'Provider B', 'tool', 'api', true)"
        )
    res2 = await check_provider_pools(pool)
    assert res2.status == "WARN"
    assert "test_prov_b" in res2.detail


@pytest.mark.asyncio
async def test_check_needs_login(pool: DbPool) -> None:
    """Detects connections in needs_login status and reports fix hints."""
    res_clean = await check_needs_login(pool)
    assert res_clean.status == "PASS"

    await seed_connection(pool, "login_prov", "login_conn", status="needs_login")
    res_warn = await check_needs_login(pool)
    assert res_warn.status == "WARN"
    assert "login_conn" in res_warn.detail
    assert "farm ai login" in res_warn.fix_hint or "farm mcp login" in res_warn.fix_hint


@pytest.mark.asyncio
async def test_check_circuits(pool: DbPool) -> None:
    """Detects open circuit breakers in connection_health."""
    res_clean = await check_circuits(pool)
    assert res_clean.status == "PASS"

    await seed_connection(pool, "circuit_prov", "circuit_conn")
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.connection_health (connection_id, circuit, consecutive_failures, last_error) "
            "values ('circuit_conn', 'open', 5, 'Rate limit exceeded') "
            "on conflict (connection_id) do update set circuit = 'open'"
        )

    res_warn = await check_circuits(pool)
    assert res_warn.status == "WARN"
    assert "circuit_conn" in res_warn.detail


@pytest.mark.asyncio
async def test_check_exhausted_accounts(pool: DbPool) -> None:
    """Reports exhausted connections with their scheduled reset times."""
    res_clean = await check_exhausted_accounts(pool)
    assert res_clean.status == "PASS"

    await seed_connection(pool, "exhaust_prov", "exhaust_conn", status="exhausted")
    res_warn = await check_exhausted_accounts(pool)
    assert res_warn.status == "WARN"
    assert "exhaust_conn" in res_warn.detail


def test_check_disk_space() -> None:
    """Validates disk space checks with realistic and extreme thresholds."""
    # Min free 100 bytes must pass on this machine
    res_pass = check_disk_space(min_free_bytes=100)
    assert res_pass.status == "PASS"

    # Extreme threshold (1 Petabyte) must fail
    res_fail = check_disk_space(min_free_bytes=10**16)
    assert res_fail.status == "FAIL"
    assert "Low disk space" in res_fail.detail


def test_check_data_dir_writable(tmp_path: Path) -> None:
    """FARM_DATA_DIR writability check passes on valid temporary folder."""
    res = check_data_dir_writable(data_directory=tmp_path)
    assert res.status == "PASS"
    assert "writable" in res.detail


def test_check_evidence_dir(tmp_path: Path) -> None:
    """Evidence directory size and count calculation."""
    # When missing
    res_empty = check_evidence_dir(data_directory=tmp_path)
    assert res_empty.status == "PASS"

    # With files
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir()
    (ev_dir / "sample1.txt").write_bytes(b"hello" * 100)
    (ev_dir / "sample2.txt").write_bytes(b"world" * 200)

    res_files = check_evidence_dir(data_directory=tmp_path)
    assert res_files.status == "PASS"
    assert "2 files" in res_files.detail


def test_check_ai_clis_mocked() -> None:
    """AI CLIs check parses version strings from subprocess outputs."""
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
        mock_run.return_value = MagicMock(returncode=0, stdout="2.1.0\n", stderr="")

        res = check_ai_clis(["claude", "hermes"])
        assert res.status == "PASS"
        assert "claude: 2.1.0" in res.detail
        assert "hermes: 2.1.0" in res.detail


@pytest.mark.asyncio
@respx.mock
async def test_run_doctor_composite(pool: DbPool, tmp_path: Path) -> None:
    """run_doctor returns list of CheckResults and formats a readable report."""
    respx.get("http://127.0.0.1:8080/health").mock(return_value=Response(200, json={"ok": True}))

    results = await run_doctor(
        pool=pool,
        data_directory=tmp_path,
        bifrost_url="http://127.0.0.1:8080",
    )
    assert len(results) >= 10
    assert all(isinstance(r, CheckResult) for r in results)

    report = format_doctor_report(results)
    assert "Result:" in report
    exit_code = doctor_exit_code(results)
    assert exit_code in (0, 1)
