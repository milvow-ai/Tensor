"""Account home and staging home management with atomic swap promotion."""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from farm.settings import data_dir


def account_home(conn_meta: dict[str, Any] | None, connection_id: str) -> Path:
    """Resolve the account home directory according to the same rules as farm ai login.

    Checks meta.home, then meta.config_dir, then defaults to <data_dir>/ai/<id>.
    """
    meta = conn_meta or {}
    config_dir_str = meta.get("home") or meta.get("config_dir")
    if not config_dir_str:
        return data_dir() / "ai" / connection_id
    return Path(config_dir_str)


def staging_home(home: Path, session_id: str | UUID) -> Path:
    """Create and return an empty sibling staging directory: <home>.login-<first 8 of id>."""
    first_8 = str(session_id)[:8]
    staging = home.parent / f"{home.name}.login-{first_8}"
    discard(staging)
    staging.mkdir(parents=True, exist_ok=True)
    return staging


def _rename_with_retry(src: Path, dst: Path) -> None:
    """Rename src to dst, retrying up to 5 times with 200ms delay on Windows."""
    max_retries = 5 if sys.platform == "win32" or os.name == "nt" else 1
    for attempt in range(max_retries):
        try:
            os.replace(src, dst)
            return
        except OSError:
            if attempt == max_retries - 1:
                raise
            time.sleep(0.2)


def promote(staging: Path, home: Path) -> None:
    """Atomic swap: existing home -> <home>.prev-<UTC timestamp>, staging -> home.

    Keeps only the newest .prev-*.
    On Windows retries each rename up to 5 times with 200 ms between tries.
    If the second rename fails, renames the prev back and raises.
    """
    prev_path: Path | None = None
    if home.exists():
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        prev_path = home.parent / f"{home.name}.prev-{ts}"
        _rename_with_retry(home, prev_path)

    try:
        _rename_with_retry(staging, home)
    except Exception:
        if prev_path is not None and prev_path.exists():
            try:
                _rename_with_retry(prev_path, home)
            except Exception:
                pass
        raise

    # Keep only the newest .prev-*
    parent = home.parent
    prefix = f"{home.name}.prev-"
    try:
        prev_entries = sorted(
            [p for p in parent.iterdir() if p.name.startswith(prefix)],
            key=lambda p: p.name,
        )
        if len(prev_entries) > 1:
            for old_prev in prev_entries[:-1]:
                discard(old_prev)
    except OSError:
        pass


def discard(staging: Path) -> None:
    """Remove staging directory or file; missing is fine."""
    try:
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
        elif staging.exists():
            staging.unlink(missing_ok=True)
    except OSError:
        pass
