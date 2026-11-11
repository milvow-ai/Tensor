"""A real custom-format dump restores data into a separate throw-away database."""

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

from farm.control.backup import create_backup, find_pg_dump, restore_public_dump


def test_intermediate_dump_cannot_hide_failed_encryption_delivery_or_stale_receipt(tmp_path: Path) -> None:
    from farm.control.backup import check_state_backup

    (tmp_path / "farm_backup_current.dump").write_bytes(b"fresh dump")
    receipt = tmp_path / "state-success.json"
    now = datetime.now(UTC)
    good = {"completed_at": now.isoformat(), "database_included": True, "offsite": True}
    assert check_state_backup(tmp_path).status == "WARN"
    for change in (
        {"database_included": False}, {"offsite": False},
        {"completed_at": (now - timedelta(hours=27)).isoformat()},
        {"completed_at": (now + timedelta(hours=1)).isoformat()},
    ):
        receipt.write_text(json.dumps(good | change))
        assert check_state_backup(tmp_path).status == "WARN"
    receipt.write_text(json.dumps(good))
    assert check_state_backup(tmp_path).status == "PASS"
    (tmp_path / "state-attempt.json").write_text(json.dumps({"success": False}))
    assert check_state_backup(tmp_path).status == "WARN"  # fresh success cannot hide a later failed attempt


def test_doctor_surfaces_quarantined_or_pending_completions_without_reading_them(tmp_path: Path) -> None:
    from farm.control.doctor import check_completion_evidence

    assert check_completion_evidence(tmp_path).status == "PASS"
    spool = tmp_path / "ai-completions" / "tenant"
    spool.mkdir(parents=True)
    for suffix in ("json", "invalid", "conflict"):
        path = spool / f"job.{suffix}"
        path.write_text("private result metadata")
        check = check_completion_evidence(tmp_path)
        assert check.status == "WARN" and "private result" not in check.detail
        path.unlink()


def test_database_backup_restores_project_context(
    db_url: str,
    scratch_db: Callable[[], str],
    tmp_path: Path,
) -> None:
    with psycopg.connect(db_url) as conn:
        conn.execute("create schema managed_test")
        conn.execute("create table managed_test.secret_table (value text)")
        conn.execute(
            "insert into project_memory(tenant_id,project,key,body) values "
            "('00000000-0000-0000-0000-000000000001','restore-test','decision','saved context')"
        )
    backup = create_backup(db_url=db_url, backup_directory=tmp_path, force_local=True, schema="public")
    target = scratch_db()
    if not str(conninfo_to_dict(target).get("dbname", "")).startswith("farm_test_"):
        raise AssertionError("restore target must be a disposable test database")
    executable = find_pg_dump(db_url=db_url, force_local=True).with_name(
        "pg_restore.exe" if os.name == "nt" else "pg_restore"
    )
    restore_public_dump(backup, target, executable=executable)
    with psycopg.connect(target) as conn:
        assert conn.execute("select to_regclass('managed_test.secret_table')").fetchone() == (None,)
        assert conn.execute("select body from project_memory where project='restore-test'").fetchone() == (
            "saved context",
        )
