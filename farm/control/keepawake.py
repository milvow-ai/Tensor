"""Windows keep-awake management using SetThreadExecutionState.

Prevents the operating system from sleeping while the Farm runs,
without altering system power, sleep, or security settings.
"""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil
import structlog

from farm.settings import data_dir as get_data_dir

if TYPE_CHECKING:
    from farm.control.doctor import CheckResult

log = structlog.get_logger(__name__)

# Windows Execution State flags
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_USER_PRESENT = 0x00000004
ES_AWAYMODE_REQUIRED = 0x00000040
ES_CONTINUOUS = 0x80000000

_in_process_active: bool = False
_atexit_registered: bool = False


@dataclass
class KeepAwakeState:
    active: bool
    pid: int
    started_at: str
    away_mode: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "pid": self.pid,
            "started_at": self.started_at,
            "away_mode": self.away_mode,
        }


def _state_file(data_directory: Path | None = None) -> Path:
    base = data_directory or get_data_dir()
    return base / "keepawake.json"


def _set_thread_execution_state(flags: int) -> int:
    """Invoke Windows SetThreadExecutionState via ctypes."""
    if sys.platform != "win32":
        return 0
    try:
        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return 0
        kernel32 = windll.kernel32
        res = int(kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))
        return res
    except Exception as exc:
        log.warning("keepawake.set_execution_state_failed", error=str(exc))
        return 0


def enable_keepawake(
    away_mode: bool = True,
    data_directory: Path | None = None,
) -> bool:
    """Enable continuous keep-awake for the current thread and process."""
    global _in_process_active, _atexit_registered

    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    if away_mode:
        flags |= ES_AWAYMODE_REQUIRED

    prev = _set_thread_execution_state(flags)
    _in_process_active = True

    # Record state file
    target = _state_file(data_directory)
    state = KeepAwakeState(
        active=True,
        pid=os.getpid(),
        started_at=datetime.now(UTC).isoformat(),
        away_mode=away_mode,
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)
    except Exception as exc:
        log.warning("keepawake.state_write_failed", error=str(exc))

    if not _atexit_registered:
        atexit.register(disable_keepawake, data_directory=data_directory)
        _atexit_registered = True

    log.info("keepawake.enabled", away_mode=away_mode, prev_state=prev)
    return True


def disable_keepawake(data_directory: Path | None = None) -> bool:
    """Release keep-awake continuous state."""
    global _in_process_active

    _set_thread_execution_state(ES_CONTINUOUS)
    _in_process_active = False

    target = _state_file(data_directory)
    if target.is_file():
        try:
            target.unlink()
        except OSError:
            pass

    log.info("keepawake.disabled")
    return True


def is_keepawake_active(data_directory: Path | None = None) -> bool:
    """Check if keep-awake is active in this process or another running Farm process."""
    global _in_process_active
    if _in_process_active:
        return True

    target = _state_file(data_directory)
    if not target.is_file():
        return False

    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("active"):
            pid = int(data.get("pid", 0))
            if pid > 0 and psutil.pid_exists(pid):
                return True
            else:
                # Stale state file from dead process
                try:
                    target.unlink()
                except OSError:
                    pass
    except Exception:
        pass

    return False


def check_keepawake(data_directory: Path | None = None) -> CheckResult:
    """Pure check function for keep-awake status."""
    from farm.control.doctor import CheckResult

    active = is_keepawake_active(data_directory=data_directory)
    if active:
        target = _state_file(data_directory)
        pid_info = ""
        if target.is_file():
            try:
                with open(target, encoding="utf-8") as f:
                    d = json.load(f)
                pid_info = f" (PID {d.get('pid')})"
            except Exception:
                pass
        return CheckResult(
            name="keepawake",
            status="PASS",
            detail=f"Keep-awake is active{pid_info}; system sleep is prevented",
        )
    else:
        return CheckResult(
            name="keepawake",
            status="WARN",
            detail="Keep-awake is not active (farm run is not currently running)",
            fix_hint="Start 'farm run' to keep the machine awake 24/7",
        )


class KeepAwake:
    """Context manager for scoped keep-awake execution."""

    def __init__(
        self,
        away_mode: bool = True,
        data_directory: Path | None = None,
    ) -> None:
        self.away_mode = away_mode
        self.data_directory = data_directory
        self._entered = False

    def __enter__(self) -> KeepAwake:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()

    def start(self) -> None:
        enable_keepawake(away_mode=self.away_mode, data_directory=self.data_directory)
        self._entered = True

    def stop(self) -> None:
        if self._entered:
            disable_keepawake(data_directory=self.data_directory)
            self._entered = False
