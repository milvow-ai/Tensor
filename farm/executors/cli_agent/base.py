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
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult

_current_effort: ContextVar[str | None] = ContextVar("_current_effort", default=None)


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


OS_ESSENTIAL_VARS = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
}

_SECRET_NAME_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|_VK\b|\bVK\b|DB_URL|DATABASE_URL)",
    re.IGNORECASE,
)
_SECRET_VAL_RE = re.compile(
    r"postgres(?:ql)?://[^:\s]+:[^@\s]+@",
    re.IGNORECASE,
)


def looks_secret(name: str, value: str) -> bool:
    """Return True if an environment variable's name or value looks like a secret/credential."""
    if _SECRET_NAME_RE.search(name):
        return True
    if _SECRET_VAL_RE.search(value):
        return True
    return False


def build_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a sanitized child environment with OS essentials and driver variables.

    Filters out any secrets (keys, tokens, passwords, DB URLs, VKs) even if requested.
    """
    child_env: dict[str, str] = {}

    for k, v in os.environ.items():
        if k.upper() in OS_ESSENTIAL_VARS:
            if not looks_secret(k, v):
                child_env[k] = v

    if extra:
        for k, v in extra.items():
            k_str = str(k)
            v_str = str(v)
            if not looks_secret(k_str, v_str):
                child_env[k_str] = v_str

    child_env["NO_COLOR"] = "1"
    return child_env


def resolve_shim(executable_path: str) -> tuple[list[str], bool]:
    """Resolve .cmd/.bat shims (e.g. npm shims or python test runner shims) to their binary target.

    Returns (resolved_argv_prefix, is_resolved_binary).
    If it is a .cmd/.bat file and cannot be resolved, returns ([executable_path], False).
    """
    p = Path(executable_path)
    ext = p.suffix.lower()
    if ext not in (".cmd", ".bat"):
        return [executable_path], True

    if not p.is_file():
        return [executable_path], False

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [executable_path], False

    # Check for Python runner script: @"python" "script" %* or "python" "script" %*
    py_match = re.search(r'(?:^|\n)\s*@?"([^"]+?\.exe)"\s+"([^"]+?)"\s+%\*', content)
    if py_match:
        target_bin = py_match.group(1)
        target_script = py_match.group(2)
        if Path(target_bin).is_file() and Path(target_script).is_file():
            return [target_bin, target_script], True

    # Check for npm shim:
    # SET "_prog=%dp0%\node.exe" ... & "%_prog%" "%dp0%\..." %*
    # or node "%dp0%\..." %*
    dp0 = p.parent
    npm_match = re.search(r'(?:&|\n)\s*(?:\$COMSPEC% & )?"(?:%_prog%|node)"\s+"([^"]+?)"\s+%\*', content)
    if not npm_match:
        npm_match = re.search(r'"%_prog%"\s+"([^"]+?)"\s+%\*', content)
    if not npm_match:
        npm_match = re.search(r'node\s+"([^"]+?)"\s+%\*', content)

    if npm_match:
        raw_script = npm_match.group(1)
        script_resolved = raw_script.replace("%dp0%", str(dp0)).replace("%~dp0", str(dp0))
        node_bin = dp0 / "node.exe"
        if not node_bin.is_file():
            which_node = shutil.which("node")
            node_cmd = which_node if which_node else "node"
        else:
            node_cmd = str(node_bin)
        return [node_cmd, str(Path(script_resolved).resolve())], True

    return [executable_path], False


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
    input_bytes: bytes | None = None,
) -> SubprocessOutput:
    start_time = time.monotonic()
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
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

    if input_bytes is not None and proc.stdin is not None:
        stdin_stream = proc.stdin

        def feed_stdin() -> None:
            try:
                stdin_stream.write(input_bytes)
                stdin_stream.flush()
            except Exception:
                pass
            finally:
                try:
                    stdin_stream.close()
                except Exception:
                    pass

        t_stdin = threading.Thread(target=feed_stdin, daemon=True)
        t_stdin.start()

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
    input_text: str | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = 30.0,
    max_bytes: int = 5 * 1024 * 1024,
) -> SubprocessOutput:
    """Run a CLI subprocess with timeout, process tree termination, and output caps.

    Does NOT use shell=True. Resolves the executable path and any .cmd/.bat shims.
    Delivers input via stdin when provided. Uses a sanitized child environment.
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

    shim_prefix, is_binary = resolve_shim(resolved_cmd)
    if not is_binary:
        return SubprocessOutput(
            exit_code=1,
            stdout="",
            stderr=f"Refusing to execute unresolvable batch file (.cmd/.bat) with untrusted text: {cmd_name}",
            duration_s=0.0,
        )

    cmd_args = [*shim_prefix, *argv[1:]]
    merged_env = build_child_env(env)

    work_dir = None
    if cwd:
        p = Path(cwd)
        if p.exists():
            work_dir = str(p.resolve())
        else:
            work_dir = str(cwd)

    input_bytes = input_text.encode("utf-8") if input_text is not None else None

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
            input_bytes,
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
    _SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    _SAFE_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
    ALLOWED_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})

    async def execute(self, req: ExecRequest) -> ExecResult:
        raise NotImplementedError


    @classmethod
    def validate_cli_identifiers(
        cls, *, session_id: Any = None, model: Any = None, effort: Any = None
    ) -> tuple[bool, str | None]:
        """Validate session_id, model, and effort against safe argv patterns to prevent flag injection."""
        if session_id is not None:
            s_str = str(session_id)
            if not cls._SAFE_IDENTIFIER_PATTERN.match(s_str):
                return False, f"Invalid session_id: {session_id!r}"
        if model is not None:
            m_str = str(model)
            if not cls._SAFE_MODEL_PATTERN.match(m_str):
                return False, f"Invalid model: {model!r}"
        if effort is not None:
            e_str = str(effort)
            if e_str not in cls.ALLOWED_EFFORTS:
                return False, f"Invalid effort: {effort!r} (must be one of {sorted(cls.ALLOWED_EFFORTS)})"
        return True, None

    @staticmethod
    def get_param(req: ExecRequest, key: str, default: Any = None) -> Any:
        return req.params.get(key, default)

    @classmethod
    def validate_confinement(
        cls, req: ExecRequest, mode: str, cwd: str | Path | None
    ) -> tuple[bool, str | None, Path | None]:
        """Validate edit mode confinement according to SEC1 rules.

        mode=edit is permitted only when meta.allow_edit is True AND cwd resolves
        inside an allowed root (meta.edit_roots, default FARM_DATA_DIR/workspaces).
        Never mkdir a caller path.
        """
        effort = req.params.get("effort")
        if effort is not None:
            ok, err_msg = cls.validate_cli_identifiers(effort=effort)
            if not ok:
                return False, err_msg, None
        _current_effort.set(str(effort) if effort is not None else None)

        if mode == "edit":
            meta = req.connection.meta or {}
            allow_edit = meta.get("allow_edit")

            # Require strict boolean True
            if allow_edit is not True:
                return (
                    False,
                    f"Edit mode is not permitted for connection '{req.connection.id}' "
                    "(meta.allow_edit is false)",
                    None,
                )

            if not cwd:
                return False, "Edit mode requires 'cwd' to be specified", None

            p = Path(cwd)
            if not p.exists():
                return False, f"Working directory does not exist: {cwd}", None

            resolved_cwd = p.resolve()
            if not resolved_cwd.is_dir():
                return False, f"Working directory is not a directory: {cwd}", None

            edit_roots = meta.get("edit_roots")
            roots: list[Path] = []
            if edit_roots is not None:
                if isinstance(edit_roots, (str, Path)):
                    raw_roots = [edit_roots]
                elif isinstance(edit_roots, list):
                    raw_roots = edit_roots
                else:
                    return False, f"Invalid edit_roots type: {type(edit_roots).__name__}", None

                for r in raw_roots:
                    r_str = str(r).strip()
                    if not r_str or r_str == ".":
                        return False, f"Invalid edit_roots entry: {r!r} (cannot be empty or '.')", None
                    p_entry = Path(r_str)
                    if not p_entry.is_absolute():
                        return False, f"Invalid edit_roots entry: {r!r} (must be an absolute path)", None
                    roots.append(p_entry.resolve())
            else:
                farm_data = Path(os.environ.get("FARM_DATA_DIR", "D:/farm-data"))
                roots = [(farm_data / "workspaces").resolve()]

            def _is_relative_to_root(path: Path, root: Path) -> bool:
                try:
                    path.relative_to(root)
                    return True
                except ValueError:
                    pass
                if sys.platform == "win32":
                    try:
                        Path(str(path).lower()).relative_to(Path(str(root).lower()))
                        return True
                    except ValueError:
                        pass
                return False

            is_inside = any(_is_relative_to_root(resolved_cwd, r) for r in roots)
            if not is_inside:
                roots_str = ", ".join(str(r) for r in roots)
                return (
                    False,
                    f"Working directory '{resolved_cwd}' is outside allowed edit roots: [{roots_str}]",
                    None,
                )

            return True, None, resolved_cwd

        # Non-edit mode (answer mode)
        if cwd:
            p = Path(cwd)
            if p.exists():
                return True, None, p.resolve()
        return True, None, None

    @staticmethod
    def build_exec_result(
        *,
        ok: bool,
        text: str = "",
        session_id: str | None = None,
        ai: str,
        model: str | None = None,
        effort: str | None = None,
        effort_applied: bool | None = None,
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

        effective_effort = effort if effort is not None else _current_effort.get()
        effective_effort_applied = effort_applied
        if effective_effort is not None and effective_effort_applied is None:
            effective_effort_applied = False if ai in ("gemini", "agy", "hermes") else True

        data: dict[str, Any] | None = None
        if ok or text:
            data = {
                "text": text,
                "json": parsed_json,
                "ai": ai,
                "model": model,
                "effort": effective_effort,
                "effort_applied": effective_effort_applied,
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
