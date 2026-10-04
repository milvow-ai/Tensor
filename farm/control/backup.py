"""PostgreSQL database backup management and rotation.

Performs pg_dump of the Farm database to FARM_DATA_DIR/backups/ with 14-day rotation.
Uses pgserver's bundled pg_dump binary in local mode; requires pg_dump on PATH for remote databases.
Pure check function validates that the latest backup is less than 26 hours old.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from farm.db.pool import get_db_url, is_local_db, is_local_url
from farm.secrets import redact
from farm.settings import data_dir as get_data_dir

if TYPE_CHECKING:
    from farm.control.doctor import CheckResult

log = structlog.get_logger(__name__)

BACKUP_PREFIX = "farm_backup_"
BACKUP_SUFFIX = ".dump"
BACKUP_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


@dataclass
class BackupInfo:
    """Metadata for an existing backup file."""

    path: Path
    filename: str
    size_bytes: int
    created_at: datetime
    age_seconds: float


def _backups_dir(backup_directory: Path | None = None) -> Path:
    if backup_directory is not None:
        if backup_directory.name == "backups" or any(backup_directory.glob("farm_backup_*")):
            b_dir = backup_directory
        else:
            b_dir = backup_directory / "backups"
    else:
        b_dir = get_data_dir() / "backups"
    b_dir.mkdir(parents=True, exist_ok=True)
    return b_dir


def find_pg_dump(db_url: str | None = None, force_local: bool = False) -> Path:
    """Locate the pg_dump executable.

    In local mode, uses pgserver's bundled pg_dump binary.
    For remote URLs, requires pg_dump to be present on the system PATH.
    """
    url = db_url or get_db_url(force_local=force_local)
    local = force_local or (is_local_url(url) if db_url else is_local_db())

    if local:
        try:
            import pgserver

            bin_name = "pg_dump.exe" if sys.platform == "win32" else "pg_dump"
            pginstall_bin = Path(pgserver.__file__).parent / "pginstall" / "bin" / bin_name
            if pginstall_bin.is_file():
                return pginstall_bin
        except Exception as exc:
            log.warning("backup.pgserver_lookup_failed", error=str(exc))

    # Look on PATH
    found = shutil.which("pg_dump")
    if found:
        return Path(found)

    if local:
        raise FileNotFoundError(
            "Could not locate pgserver's bundled pg_dump binary for local database backup."
        )
    else:
        raise FileNotFoundError(
            "pg_dump binary not found on PATH. Remote database backup requires PostgreSQL "
            "client tools (pg_dump) to be installed and available on PATH."
        )


def rotate_backups(
    backup_directory: Path | None = None,
    max_age_days: int = 14,
) -> list[Path]:
    """Delete backups older than max_age_days (default 14 days).

    Returns the list of removed backup file paths.
    """
    b_dir = _backups_dir(backup_directory)
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    removed: list[Path] = []

    for item in b_dir.iterdir():
        if not item.is_file() or not item.name.startswith(BACKUP_PREFIX):
            continue
        try:
            mtime = datetime.fromtimestamp(item.stat().st_mtime, tz=UTC)
            if mtime < cutoff:
                item.unlink()
                removed.append(item)
                log.info("backup.rotated", path=str(item), age_days=(datetime.now(UTC) - mtime).days)
        except OSError as exc:
            log.warning("backup.rotation_error", path=str(item), error=str(exc))

    return removed


def get_last_backup_info(backup_directory: Path | None = None) -> BackupInfo | None:
    """Find and return metadata for the most recent backup, or None if no backups exist."""
    b_dir = _backups_dir(backup_directory)
    candidates: list[tuple[datetime, Path, int]] = []

    for item in b_dir.iterdir():
        if not item.is_file() or not item.name.startswith(BACKUP_PREFIX):
            continue
        try:
            st = item.stat()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
            candidates.append((mtime, item, st.st_size))
        except OSError:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    latest_time, latest_path, size = candidates[0]
    now = datetime.now(UTC)
    age_s = max(0.0, (now - latest_time).total_seconds())

    return BackupInfo(
        path=latest_path,
        filename=latest_path.name,
        size_bytes=size,
        created_at=latest_time,
        age_seconds=age_s,
    )


def create_backup(
    db_url: str | None = None,
    backup_directory: Path | None = None,
    force_local: bool = False,
    timeout_s: float = 300.0,
) -> Path:
    """Create a pg_dump backup and enforce 14-day retention rotation.

    Subprocesses are executed via standard synchronous execution (safe for Windows SelectorEventLoop).
    """
    resolved_url = db_url or get_db_url(force_local=force_local)
    pg_dump_bin = find_pg_dump(resolved_url, force_local=force_local)

    b_dir = _backups_dir(backup_directory)
    timestamp = datetime.now(UTC).strftime(BACKUP_TIMESTAMP_FMT)
    target_file = b_dir / f"{BACKUP_PREFIX}{timestamp}{BACKUP_SUFFIX}"

    # Prepare command: pg_dump --dbname <url> -F c -f <target_file>
    # Pass password via PGPASSWORD environment variable when possible to avoid command-line exposure
    env = os.environ.copy()
    cmd = [str(pg_dump_bin), "--dbname", resolved_url, "-F", "c", "-f", str(target_file)]

    log.info("backup.started", target=str(target_file))
    try:
        res = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if res.returncode != 0:
            err = redact(res.stderr.strip() or f"pg_dump exited with code {res.returncode}")
            if target_file.is_file():
                try:
                    target_file.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Database backup failed: {err}")
    except subprocess.TimeoutExpired:
        if target_file.is_file():
            try:
                target_file.unlink()
            except OSError:
                pass
        raise TimeoutError(f"Database backup timed out after {timeout_s}s") from None

    # Rotate old backups
    rotate_backups(backup_directory=b_dir, max_age_days=14)

    size = target_file.stat().st_size
    log.info("backup.completed", target=str(target_file), size_bytes=size)
    return target_file


def check_last_backup(
    backup_directory: Path | None = None,
    max_age_hours: float = 26.0,
) -> CheckResult:
    """Pure check function validating that a backup exists and is < 26 hours old."""
    from farm.control.doctor import CheckResult

    info = get_last_backup_info(backup_directory=backup_directory)
    if info is None:
        return CheckResult(
            name="last_backup_age",
            status="WARN",
            detail="No database backups found in backups directory",
            fix_hint="Run 'farm backup' to create an initial database backup",
        )

    age_hours = info.age_seconds / 3600.0
    size_kb = info.size_bytes / 1024.0

    if age_hours < max_age_hours:
        return CheckResult(
            name="last_backup_age",
            status="PASS",
            detail=f"Last backup is {age_hours:.1f}h old ({info.filename}, {size_kb:.1f} KB)",
        )
    else:
        thresh = f"{max_age_hours:.0f}h threshold"
        return CheckResult(
            name="last_backup_age",
            status="WARN",
            detail=f"Last backup is stale: {age_hours:.1f}h old (>= {thresh}, {info.filename})",
            fix_hint="Run 'farm backup' to create a fresh backup",
        )
