"""Unit and integration tests for PostgreSQL database backup and 14-day rotation."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from farm.control.backup import (
    BACKUP_PREFIX,
    BACKUP_SUFFIX,
    check_last_backup,
    create_backup,
    find_pg_dump,
    get_last_backup_info,
    rotate_backups,
)


def test_find_pg_dump_local() -> None:
    """find_pg_dump locates the pgserver-bundled pg_dump in local mode."""
    bin_path = find_pg_dump(force_local=True)
    assert bin_path.is_file()
    assert "pg_dump" in bin_path.name.lower()


def test_find_pg_dump_remote_missing() -> None:
    """Remote database URL without pg_dump on PATH raises clear FileNotFoundError."""
    remote_url = "postgresql://user:pass@db.supabase.co:5432/postgres"
    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="pg_dump binary not found on PATH"):
            find_pg_dump(db_url=remote_url, force_local=False)


def test_rotate_backups(tmp_path: Path) -> None:
    """rotate_backups removes backups older than 14 days and preserves recent ones."""
    now = datetime.now(UTC)
    b_dir = tmp_path / "backups"
    b_dir.mkdir()

    # Create 3 files: 5 days old, 15 days old, 30 days old
    f_recent = b_dir / f"{BACKUP_PREFIX}20261001_000000{BACKUP_SUFFIX}"
    f_recent.write_bytes(b"recent_dump")
    os.utime(f_recent, ((now - timedelta(days=5)).timestamp(), (now - timedelta(days=5)).timestamp()))

    f_old1 = b_dir / f"{BACKUP_PREFIX}20260915_000000{BACKUP_SUFFIX}"
    f_old1.write_bytes(b"old1_dump")
    os.utime(f_old1, ((now - timedelta(days=15)).timestamp(), (now - timedelta(days=15)).timestamp()))

    f_old2 = b_dir / f"{BACKUP_PREFIX}20260901_000000{BACKUP_SUFFIX}"
    f_old2.write_bytes(b"old2_dump")
    os.utime(f_old2, ((now - timedelta(days=30)).timestamp(), (now - timedelta(days=30)).timestamp()))

    removed = rotate_backups(backup_directory=b_dir, max_age_days=14)
    assert len(removed) == 2
    assert f_old1 in removed
    assert f_old2 in removed
    assert f_recent.is_file()
    assert not f_old1.exists()
    assert not f_old2.exists()


def test_check_last_backup_fresh(tmp_path: Path) -> None:
    """Backup created 2 hours ago returns PASS."""
    b_dir = tmp_path / "backups"
    b_dir.mkdir()
    f = b_dir / f"{BACKUP_PREFIX}20261004_100000{BACKUP_SUFFIX}"
    f.write_bytes(b"dump_content_12345")
    two_hours_ago = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(f, (two_hours_ago, two_hours_ago))

    res = check_last_backup(backup_directory=b_dir, max_age_hours=26.0)
    assert res.status == "PASS"
    assert "2.0h old" in res.detail or "1.9h old" in res.detail or "2.1h old" in res.detail


def test_check_last_backup_stale(tmp_path: Path) -> None:
    """Backup created 30 hours ago (> 26h) returns WARN."""
    b_dir = tmp_path / "backups"
    b_dir.mkdir()
    f = b_dir / f"{BACKUP_PREFIX}20261002_100000{BACKUP_SUFFIX}"
    f.write_bytes(b"old_content")
    stale_time = (datetime.now(UTC) - timedelta(hours=30)).timestamp()
    os.utime(f, (stale_time, stale_time))

    res = check_last_backup(backup_directory=b_dir, max_age_hours=26.0)
    assert res.status == "WARN"
    assert "stale" in res.detail
    assert res.fix_hint != ""


def test_check_last_backup_none(tmp_path: Path) -> None:
    """No backups present returns WARN with hint."""
    b_dir = tmp_path / "backups"
    b_dir.mkdir()
    res = check_last_backup(backup_directory=b_dir)
    assert res.status == "WARN"
    assert "No database backups found" in res.detail


def test_get_last_backup_info(tmp_path: Path) -> None:
    """get_last_backup_info accurately picks the most recent backup."""
    b_dir = tmp_path / "backups"
    b_dir.mkdir()
    now = datetime.now(UTC)

    f1 = b_dir / f"{BACKUP_PREFIX}1{BACKUP_SUFFIX}"
    f1.write_bytes(b"data1")
    t1 = (now - timedelta(hours=5)).timestamp()
    os.utime(f1, (t1, t1))

    f2 = b_dir / f"{BACKUP_PREFIX}2{BACKUP_SUFFIX}"
    f2.write_bytes(b"data2")
    t2 = (now - timedelta(hours=1)).timestamp()
    os.utime(f2, (t2, t2))

    info = get_last_backup_info(backup_directory=b_dir)
    assert info is not None
    assert info.filename == f2.name
    assert info.size_bytes == len(b"data2")


def test_create_backup_integration(db_url: str, tmp_path: Path) -> None:
    """create_backup against local test database produces a valid pg_dump file."""
    backup_file = create_backup(db_url=db_url, backup_directory=tmp_path, force_local=True)
    assert backup_file.is_file()
    assert backup_file.stat().st_size > 0
    assert backup_file.name.startswith(BACKUP_PREFIX)
    assert backup_file.name.endswith(BACKUP_SUFFIX)
