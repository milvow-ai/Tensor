"""PostgreSQL database backup management and rotation.

Performs pg_dump of the Farm database to FARM_DATA_DIR/backups/ with 14-day rotation.
Uses pgserver's bundled pg_dump binary in local mode; requires pg_dump on PATH for remote databases.
Pure check function validates that the latest backup is less than 26 hours old.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import psycopg
import structlog
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from farm.db.pool import get_db_url, is_local_db, is_local_url
from farm.secrets import redact
from farm.settings import data_dir as get_data_dir

if TYPE_CHECKING:
    from farm.control.doctor import CheckResult

log = structlog.get_logger(__name__)

BACKUP_PREFIX = "farm_backup_"
BACKUP_SUFFIX = ".dump"
BACKUP_TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


def check_state_backup(backup_directory: Path, *, require_offsite: bool = True) -> CheckResult:
    """An archive/delivery failure cannot be hidden by a fresh intermediate pg_dump."""
    from farm.control.doctor import CheckResult

    try:
        path = backup_directory / "state-success.json"
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ValueError("invalid backup receipt")
        value = json.loads(path.read_text(encoding="utf-8"))
        attempt = backup_directory / "state-attempt.json"
        if attempt.exists():
            if attempt.is_symlink() or attempt.stat().st_size > 4096:
                raise ValueError("invalid backup attempt receipt")
            last_attempt = json.loads(attempt.read_text(encoding="utf-8"))
            if not isinstance(last_attempt, dict) or last_attempt.get("success") is not True:
                raise ValueError("latest backup attempt failed")
        completed = datetime.fromisoformat(value["completed_at"])
        age = (datetime.now(UTC) - completed).total_seconds()
        if age < -300 or age >= 26 * 3600 or value.get("database_included") is not True:
            raise ValueError("stale or incomplete backup")
        if require_offsite and value.get("offsite") is not True:
            raise ValueError("no offsite receipt")
    except (OSError, ValueError, TypeError, KeyError):
        return CheckResult(
            name="encrypted_state_backup", status="WARN",
            detail="No recent complete encrypted database/state backup with required delivery",
            fix_hint="Inspect the backup timer and off-host delivery; run an isolated restore drill",
        )
    return CheckResult(
        name="encrypted_state_backup", status="PASS",
        detail=f"Complete encrypted backup/delivery succeeded {max(0, age) / 3600:.1f}h ago",
    )


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


def split_password(url: str) -> tuple[str, str | None]:
    """``url`` as a libpq connection string without its password, and the password (None if it has none)."""
    params = conninfo_to_dict(url)
    password = params.pop("password", None)
    return make_conninfo("", **params), None if password is None else str(password)


def restore_public_dump(archive: Path, db_url: str, *, executable: str | Path = "pg_restore") -> None:
    """Restore into an initialized public schema without dropping it or replacing existing Farm data."""
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        present = conn.execute("select to_regclass('public.farm_settings') is not null").fetchone()
        if present is None or present[0]:
            raise ValueError("The restore database must be empty of Farm tables")
    dsn, password = split_password(db_url)
    env = os.environ.copy()
    env.pop("PGPASSWORD", None)
    if password is not None:
        env["PGPASSWORD"] = password
    entries = subprocess.run(
        [str(executable), "--list", str(archive)], capture_output=True, check=True, timeout=60,
    ).stdout
    # A scoped pg_dump emits CREATE SCHEMA public even though the target already has that schema.
    with tempfile.TemporaryDirectory(prefix="farm-restore-") as directory:
        toc = Path(directory) / "toc.list"
        create_public = re.compile(rb"\d+;\s+\d+\s+\d+\s+SCHEMA - public ")
        selected_entries = [line for line in entries.splitlines() if not create_public.match(line)]
        toc.write_bytes(b"\n".join(selected_entries) + b"\n")
        restored = subprocess.run(
            [str(executable), "--exit-on-error", "--single-transaction", "--no-owner", "--schema=public",
             "--use-list", str(toc), "--dbname", dsn, str(archive)],
            env=env, capture_output=True, text=True, timeout=600,
        )
        if restored.returncode:
            raise RuntimeError("PostgreSQL rejected the restore. Check target roles, schema and server logs.")


def find_pg_dump(db_url: str | None = None, force_local: bool = False) -> Path:
    """Locate the pg_dump executable.

    In local mode, uses pgserver's bundled pg_dump binary.
    For remote URLs, requires pg_dump to be present on the system PATH.
    """
    url = db_url or get_db_url(force_local=force_local)
    local = os.environ.get("FARM_REQUIRE_EXTERNAL_DB") != "1" and (
        force_local or (is_local_url(url) if db_url else is_local_db())
    )

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
        if not (item.is_file() and item.name.startswith(BACKUP_PREFIX) and item.name.endswith(BACKUP_SUFFIX)):
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
        if not (item.is_file() and item.name.startswith(BACKUP_PREFIX) and item.name.endswith(BACKUP_SUFFIX)):
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
    *,
    schema: str | None = None,
) -> Path:
    """Create a pg_dump backup and enforce 14-day retention rotation.

    Subprocesses are executed via standard synchronous execution (safe for Windows SelectorEventLoop).
    """
    resolved_url = db_url or get_db_url(force_local=force_local)
    pg_dump_bin = find_pg_dump(resolved_url, force_local=force_local)

    b_dir = _backups_dir(backup_directory)
    timestamp = datetime.now(UTC).strftime(BACKUP_TIMESTAMP_FMT)
    target_file = b_dir / f"{BACKUP_PREFIX}{timestamp}_{uuid4().hex[:8]}{BACKUP_SUFFIX}"
    partial_file = target_file.with_suffix(".part")

    # pg_dump --dbname <connection without its password> -F c -f <target_file>; the password travels in
    # PGPASSWORD, never on the command line (every user of a server can read each process's arguments).
    env = os.environ.copy()
    dbname, password = split_password(resolved_url)
    if password is not None:
        env["PGPASSWORD"] = password
    cmd = [str(pg_dump_bin), "--dbname", dbname, "-F", "c", "-f", str(partial_file)]
    if schema is not None:
        cmd.append(f"--schema={schema}")

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
            if partial_file.is_file():
                try:
                    partial_file.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Database backup failed: {err}")
    except subprocess.TimeoutExpired:
        if partial_file.is_file():
            try:
                partial_file.unlink()
            except OSError:
                pass
        raise TimeoutError(f"Database backup timed out after {timeout_s}s") from None

    if partial_file.stat().st_size == 0:
        partial_file.unlink()
        raise RuntimeError("Database backup is empty")
    partial_file.chmod(0o600)
    with partial_file.open("r+b") as stream:
        os.fsync(stream.fileno())
    os.replace(partial_file, target_file)
    if os.name == "posix":
        directory = os.open(b_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

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
