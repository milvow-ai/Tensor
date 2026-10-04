"""Process heartbeat recording and checking for 24/7 operations.

Processes write a heartbeat file (and optional DB row) every 15 s.
Doctor checks that the command consumer / farm run heartbeat is fresh (< 60 s).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from farm.settings import data_dir as get_data_dir

if TYPE_CHECKING:
    from farm.control.doctor import CheckResult
    from farm.db.pool import DbPool

log = structlog.get_logger(__name__)


@dataclass
class HeartbeatInfo:
    """Snapshot of a process heartbeat."""

    component: str
    timestamp: datetime
    pid: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "timestamp": self.timestamp.isoformat(),
            "pid": self.pid,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeartbeatInfo:
        ts_str = data.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except Exception:
            ts = datetime.now(UTC)
        return cls(
            component=str(data.get("component", "unknown")),
            timestamp=ts,
            pid=int(data.get("pid", 0)),
            extra=data.get("extra", {}) if isinstance(data.get("extra"), dict) else {},
        )


def _heartbeats_dir(data_directory: Path | None = None) -> Path:
    base = data_directory or get_data_dir()
    hb_dir = base / "heartbeats"
    hb_dir.mkdir(parents=True, exist_ok=True)
    return hb_dir


def record_heartbeat(
    component: str = "command_consumer",
    data_directory: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> HeartbeatInfo:
    """Record a heartbeat timestamp and process info to disk atomically."""
    now = datetime.now(UTC)
    pid = os.getpid()
    info = HeartbeatInfo(component=component, timestamp=now, pid=pid, extra=extra or {})

    hb_dir = _heartbeats_dir(data_directory)
    target_file = hb_dir / f"{component}.json"

    # Write atomically via temp file in same directory
    payload = json.dumps(info.to_dict(), indent=2)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{component}_", suffix=".tmp", dir=str(hb_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, target_file)
    except Exception as exc:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        log.warning("heartbeat.record_failed", component=component, error=str(exc))
        raise

    return info


async def record_heartbeat_db(
    pool: DbPool,
    component: str = "command_consumer",
    extra: dict[str, Any] | None = None,
) -> None:
    """Optionally record heartbeat to database if table exists."""
    now = datetime.now(UTC)
    pid = os.getpid()
    try:
        async with pool.connection() as conn:
            await conn.execute(
                "create table if not exists public.farm_heartbeats ("
                "component text primary key, last_heartbeat timestamptz not null, pid int, extra jsonb)",
            )
            from psycopg.types.json import Jsonb

            await conn.execute(
                "insert into public.farm_heartbeats (component, last_heartbeat, pid, extra) "
                "values (%s, %s, %s, %s) "
                "on conflict (component) do update set "
                "last_heartbeat = excluded.last_heartbeat, pid = excluded.pid, extra = excluded.extra",
                (component, now, pid, Jsonb(extra or {})),
            )
    except Exception as exc:
        log.debug("heartbeat.db_skipped", component=component, error=str(exc))


def read_heartbeat(
    component: str = "command_consumer",
    data_directory: Path | None = None,
) -> HeartbeatInfo | None:
    """Read the latest heartbeat from disk for the given component."""
    hb_dir = _heartbeats_dir(data_directory)
    target_file = hb_dir / f"{component}.json"
    if not target_file.is_file():
        return None

    try:
        with open(target_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return HeartbeatInfo.from_dict(data)
    except Exception as exc:
        log.warning("heartbeat.read_failed", component=component, error=str(exc))
    return None


def check_heartbeat(
    component: str = "command_consumer",
    max_age_s: float = 60.0,
    data_directory: Path | None = None,
) -> CheckResult:
    """Pure check function for heartbeat staleness."""
    from farm.control.doctor import CheckResult

    info = read_heartbeat(component, data_directory=data_directory)
    if info is None:
        return CheckResult(
            name=f"heartbeat_{component}",
            status="FAIL",
            detail=f"No heartbeat found for '{component}'",
            fix_hint=f"Ensure '{component}' is running",
        )

    now = datetime.now(UTC)
    age_s = (now - info.timestamp).total_seconds()
    if age_s < 0:
        age_s = 0.0

    if age_s <= max_age_s:
        return CheckResult(
            name=f"heartbeat_{component}",
            status="PASS",
            detail=f"'{component}' heartbeat {age_s:.1f}s ago (PID {info.pid})",
        )
    else:
        return CheckResult(
            name=f"heartbeat_{component}",
            status="FAIL",
            detail=f"'{component}' heartbeat is stale: {age_s:.1f}s old (> {max_age_s:.0f}s, PID {info.pid})",
            fix_hint=f"Check if '{component}' has crashed or hung",
        )


class HeartbeatWriter:
    """Background task writing heartbeats every interval_s (default 15s)."""

    def __init__(
        self,
        component: str = "command_consumer",
        interval_s: float = 15.0,
        data_directory: Path | None = None,
        pool: DbPool | None = None,
        extra_fn: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.component = component
        self.interval_s = interval_s
        self.data_directory = data_directory
        self.pool = pool
        self.extra_fn = extra_fn
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("heartbeat_writer.started", component=self.component, interval_s=self.interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("heartbeat_writer.stopped", component=self.component)

    async def _loop(self) -> None:
        while self._running:
            try:
                extra = self.extra_fn() if self.extra_fn else {}
                record_heartbeat(self.component, self.data_directory, extra=extra)
                if self.pool is not None:
                    await record_heartbeat_db(self.pool, self.component, extra=extra)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("heartbeat_writer.tick_failed", component=self.component, error=str(exc))

            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                break
