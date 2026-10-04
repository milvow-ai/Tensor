"""Base utilities and subprocess runner for CLI agent executors."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult


@dataclass
class SubprocessOutput:
    """Result of running a CLI subprocess."""

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


def _read_stream(stream: IO[bytes] | None, max_bytes: int) -> str:
    """Read stream up to max_bytes, draining remainder if exceeded."""
    if stream is None:
        return ""
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            if total + len(chunk) > max_bytes:
                allowed = max_bytes - total
                if allowed > 0:
                    chunks.append(chunk[:allowed])
                chunks.append(b"\n[OUTPUT TRUNCATED]")
                try:
                    while stream.read(64 * 1024):
                        pass
                except Exception:
                    pass
                break
            chunks.append(chunk)
            total += len(chunk)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass
    return b"".join(chunks).decode("utf-8", errors="replace")


def _kill_process_tree(pid: int) -> None:
    """Kill process and all child processes recursively."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), 9)
            except Exception:
                try:
                    os.killpg(pid, 9)
                except Exception:
                    os.kill(pid, 9)
    except Exception:
        pass


def clean_cli_text(text: str) -> str:
    """Strip ANSI escape codes from text."""
    ansi_regex = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
    return ansi_regex.sub("", text).strip()


def parse_json_or_none(text: str) -> Any:
    """Attempt to parse text as JSON, including finding embedded JSON."""
    cleaned = clean_cli_text(text)
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Try finding the first '{' and last '}'
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            pass

    # Try finding the first '[' and last ']'
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:
            pass

    return None


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON lines into a list of dicts."""
    results: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = parse_json_or_none(line)
        if isinstance(parsed, dict):
            results.append(parsed)
    return results


def parse_reset_at(text: str, data: dict[str, Any] | None = None) -> datetime | None:
    """Extract a reset timestamp from JSON payload or error text."""
    if data:
        for key in ("reset_at", "next_reset_at", "resets_at", "reset_time"):
            val = data.get(key)
            if isinstance(val, (int, float)):
                return datetime.fromtimestamp(val, tz=UTC)
            if isinstance(val, str):
                try:
                    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt
                except Exception:
                    pass

    # Search text for ISO timestamps (e.g. 2026-10-04T12:00:00Z)
    iso_pattern = re.compile(r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b")
    match = iso_pattern.search(text)
    if match:
        raw = match.group(1).replace(" ", "T")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except Exception:
            pass

    # Search text for relative duration: "resets in X hours/minutes/seconds"
    rel_pattern = re.compile(r"resets?\s+in\s+(\d+)\s*(hour|hr|minute|min|second|sec)s?", re.IGNORECASE)
    rel_match = rel_pattern.search(text)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        now = datetime.now(UTC)
        if unit.startswith("hour") or unit.startswith("hr"):
            return datetime.fromtimestamp(now.timestamp() + val * 3600, tz=UTC)
        if unit.startswith("min"):
            return datetime.fromtimestamp(now.timestamp() + val * 60, tz=UTC)
        if unit.startswith("sec"):
            return datetime.fromtimestamp(now.timestamp() + val, tz=UTC)

    return None


def extract_token_usage(data: dict[str, Any] | None) -> dict[str, float]:
    """Extract standard token counts from executor data dict."""
    if not data:
        return {}
    usage: dict[str, float] = {}
    nested_usage = data.get("usage")
    source = nested_usage if isinstance(nested_usage, dict) else data

    key_map = {
        "input_tokens": "input_tokens",
        "prompt_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "completion_tokens": "output_tokens",
        "reasoning_tokens": "reasoning_tokens",
        "thinking_tokens": "reasoning_tokens",
        "cache_read_tokens": "cache_read_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
        "cache_creation_input_tokens": "cache_write_tokens",
        "cache_write_tokens": "cache_write_tokens",
        "total_tokens": "total_tokens",
    }
    for k, target in key_map.items():
        if k in source and isinstance(source[k], (int, float)):
            usage[target] = float(source[k])
    return usage


def extract_cost_usd(data: dict[str, Any] | None) -> Decimal:
    """Extract dollar cost as Decimal from executor data dict."""
    if not data:
        return Decimal(0)
    for key in ("total_cost_usd", "estimated_cost_usd", "cost_usd", "cost"):
        if key in data and data[key] is not None:
            try:
                return Decimal(str(data[key]))
            except Exception:
                pass
    return Decimal(0)


class _ProcessState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen[bytes] | None = None
        self.cancelled = False


def _run_sync_worker(
    state: _ProcessState,
    cmd_name: str,
    cmd_args: list[str],
    work_dir: str | None,
    merged_env: dict[str, str],
    timeout_s: float,
    max_bytes: int,
) -> SubprocessOutput:
    start_time = time.monotonic()
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": work_dir,
        "env": merged_env,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    with state.lock:
        if state.cancelled:
            return SubprocessOutput(
                exit_code=-1,
                stdout="",
                stderr="Process cancelled before start",
                duration_s=0.0,
                timed_out=False,
            )
        try:
            proc = subprocess.Popen(cmd_args, **popen_kwargs)
            state.proc = proc
        except Exception as exc:
            duration_s = time.monotonic() - start_time
            return SubprocessOutput(
                exit_code=127,
                stdout="",
                stderr=f"Failed to start process {cmd_name}: {exc}",
                duration_s=duration_s,
            )

    stdout_result: list[str] = [""]
    stderr_result: list[str] = [""]

    def read_stdout() -> None:
        stdout_result[0] = _read_stream(proc.stdout, max_bytes)

    def read_stderr() -> None:
        stderr_result[0] = _read_stream(proc.stderr, max_bytes)

    t_out = threading.Thread(target=read_stdout, daemon=True)
    t_err = threading.Thread(target=read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True

    if timed_out:
        duration_s = time.monotonic() - start_time
        if proc.pid is not None:
            _kill_process_tree(proc.pid)
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5.0)
            except Exception:
                pass
        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)
        return SubprocessOutput(
            exit_code=-1,
            stdout="",
            stderr=f"Process timed out after {timeout_s}s",
            duration_s=duration_s,
            timed_out=True,
        )

    duration_s = time.monotonic() - start_time
    t_out.join(timeout=5.0)
    t_err.join(timeout=5.0)
    return SubprocessOutput(
        exit_code=proc.returncode if proc.returncode is not None else 0,
        stdout=stdout_result[0],
        stderr=stderr_result[0],
        duration_s=duration_s,
        timed_out=False,
    )


async def run_cli_process(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = 30.0,
    max_bytes: int = 5 * 1024 * 1024,
) -> SubprocessOutput:
    """Run a CLI subprocess with timeout, process tree termination, and output caps.

    Does NOT use shell=True. Resolves the executable path using shutil.which.
    Runs via a worker thread (asyncio.to_thread) around subprocess.Popen.
    """
    if not argv:
        return SubprocessOutput(
            exit_code=127,
            stdout="",
            stderr="Empty argv provided",
            duration_s=0.0,
        )

    cmd_name = argv[0]
    resolved_cmd = shutil.which(cmd_name)
    if not resolved_cmd:
        return SubprocessOutput(
            exit_code=127,
            stdout="",
            stderr=f"Executable not found on PATH: {cmd_name}",
            duration_s=0.0,
        )

    # Windows .cmd/.bat wrapping if needed
    cmd_args = [resolved_cmd, *argv[1:]]

    # Env overlay
    merged_env = os.environ.copy()
    if env:
        for k, v in env.items():
            merged_env[str(k)] = str(v)

    work_dir = None
    if cwd:
        p = Path(cwd)
        try:
            p.mkdir(parents=True, exist_ok=True)
            work_dir = str(p)
        except Exception:
            work_dir = str(cwd)

    state = _ProcessState()
    try:
        return await asyncio.to_thread(
            _run_sync_worker,
            state,
            cmd_name,
            cmd_args,
            work_dir,
            merged_env,
            timeout_s,
            max_bytes,
        )
    except asyncio.CancelledError:
        with state.lock:
            state.cancelled = True
            proc = state.proc
        if proc is not None and proc.pid is not None:
            _kill_process_tree(proc.pid)
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5.0)
            except Exception:
                pass
        raise


class BaseCliAgentExecutor:
    """Base class providing shared helper methods for CLI agent executors."""

    @staticmethod
    def get_param(req: ExecRequest, key: str, default: Any = None) -> Any:
        return req.params.get(key, default)

    @staticmethod
    def build_exec_result(
        *,
        ok: bool,
        text: str = "",
        session_id: str | None = None,
        ai: str,
        model: str | None = None,
        connection: ConnectionView,
        raw_data: dict[str, Any] | None = None,
        parsed_json: Any = None,
        duration_s: float = 0.0,
        error_kind: ErrorKind | None = None,
        error: str | None = None,
        reset_at: datetime | None = None,
        retry_after_s: float | None = None,
    ) -> ExecResult:
        units_used = extract_token_usage(raw_data)
        cost_usd = extract_cost_usd(raw_data)
        latency_ms = int(duration_s * 1000)

        data: dict[str, Any] | None = None
        if ok or text:
            data = {
                "text": text,
                "json": parsed_json,
                "ai": ai,
                "model": model,
                "connection_id": connection.id,
                "session_id": session_id,
                "usage": units_used,
                "cost_usd": float(cost_usd),
                "duration_s": duration_s,
            }

        return ExecResult(
            ok=ok,
            data=data,
            found=True if ok else None,
            units_used=units_used,
            cost_usd=cost_usd,
            error_kind=error_kind,
            error=error,
            reset_at=reset_at,
            retry_after_s=retry_after_s,
            latency_ms=latency_ms,
        )
