"""Read-only, bounded Codex app-server account quota probe."""

from __future__ import annotations

import asyncio
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

MAX_OUTPUT = 256 * 1024
TIMEOUT_S = 15.0
_ACTIVE: dict[threading.Event, subprocess.Popen[Any]] = {}
_ACTIVE_LOCK = threading.Lock()

_SECRET_ENV_PATTERNS = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|_VK\b|\bVK\b|AUTH|CREDENTIAL)",
    re.IGNORECASE,
)


def _terminate(proc: subprocess.Popen[Any]) -> None:
    """Best-effort bounded termination of the spawned process."""
    if proc.poll() is not None:
        return
    pid = proc.pid
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
    else:
        import signal

        getpgid_fn = getattr(os, "getpgid", None)
        killpg_fn = getattr(os, "killpg", None)
        sigkill = getattr(signal, "SIGKILL", 9)
        if callable(killpg_fn) and callable(getpgid_fn):
            try:
                pgid = getpgid_fn(pid)
                killpg_fn(pgid, sigkill)
            except Exception:
                try:
                    os.kill(pid, sigkill)
                except Exception:
                    pass
        else:
            try:
                os.kill(pid, sigkill)
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


def _reap(proc: subprocess.Popen[Any], timeout: float = 2.0) -> None:
    """Wait for child process to exit completely on all paths."""
    try:
        proc.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, Exception):
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass


def _wsl_path(path: str) -> str:
    """Convert Windows path (e.g. D:\\a\\b) to WSL path (/mnt/d/a/b)."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not m:
        return path.replace("\\", "/")
    drive = m.group(1).lower()
    rest = m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def _argv(meta: dict[str, Any]) -> list[str] | None:
    raw = meta.get("cli_path", "codex")
    prefix = meta.get("cli_prefix")
    home = meta.get("home") or meta.get("config_dir")

    if prefix is not None:
        if not isinstance(prefix, list) or not prefix or not all(isinstance(p, str) for p in prefix):
            return None
        # Support only the observed named-user WSL launcher; never evaluate a
        # caller-provided shell prefix or use WSL's default user.
        base_bin = Path(prefix[0]).name.lower()
        if base_bin in ("wsl.exe", "wsl") and os.name == "nt":
            if len(prefix) < 7 or prefix[1] != "-d" or prefix[3] != "-u" or prefix[5:7] != ["-e", "env"]:
                return None
            distro, user = prefix[2], prefix[4]
            if not distro or not user:
                return None
            if prefix[7:] not in ([], ["-u", "DBUS_SESSION_BUS_ADDRESS"]):
                return None
            if home:
                wsl_home = str(home)
                if not wsl_home.startswith("/") or "\x00" in wsl_home:
                    return None
            else:
                try:
                    discovered = subprocess.run(
                        [
                            str(prefix[0]),
                            "-d",
                            distro,
                            "-u",
                            user,
                            "-e",
                            "printenv",
                            "HOME",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        timeout=8,
                        check=False,
                    )
                    wsl_home = discovered.stdout.decode("utf-8", "strict").strip()
                    if discovered.returncode or len(wsl_home) > 4096 or not wsl_home.startswith("/"):
                        return None
                except (OSError, subprocess.TimeoutExpired, UnicodeError):
                    return None
            configured_home = bool(home)
            codex_home = wsl_home if configured_home else f"{wsl_home.rstrip('/')}/.codex"
            return [
                str(prefix[0]),
                "-d",
                distro,
                "-u",
                user,
                "--cd",
                wsl_home,
                "-e",
                "env",
                "-u",
                "OPENAI_API_KEY",
                "-u",
                "ANTHROPIC_API_KEY",
                f"CODEX_HOME={codex_home}",
                "NO_COLOR=1",
                str(raw),
                "app-server",
                "--listen",
                "stdio://",
            ]
        # Any other prefix is unsupported because home cannot safely be bound to the child.
        return None

    found = shutil.which(str(raw))
    if not found:
        return None

    try:
        from farm.executors.cli_agent.base import resolve_shim

        shim, _ = resolve_shim(found)
        return [*shim, "app-server", "--listen", "stdio://"]
    except Exception:
        return [found, "app-server", "--listen", "stdio://"]


def _clean_env(meta: dict[str, Any], home: str) -> dict[str, str]:
    """Preserve configured native home and meta env, clearing inherited secrets."""
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if _SECRET_ENV_PATTERNS.search(k):
            continue
        upper_k = k.upper()
        if upper_k.startswith(
            (
                "OPENAI_",
                "ANTHROPIC_",
                "GEMINI_",
                "MISTRAL_",
                "GROQ_",
                "COHERE_",
                "AWS_",
                "AZURE_",
                "GH_",
                "GITHUB_",
            )
        ):
            continue
        env[k] = v

    meta_env = meta.get("env")
    if isinstance(meta_env, dict):
        for k, v in meta_env.items():
            env[str(k)] = str(v)

    env["CODEX_HOME"] = str(home)
    env["NO_COLOR"] = "1"
    return env


def _is_finite_nonnegative(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value) and value >= 0
    return False


def _is_valid_date(d: Any) -> bool:
    if not isinstance(d, str) or len(d) != 10:
        return False
    try:
        datetime.date.fromisoformat(d)
        return True
    except (ValueError, TypeError):
        return False


def _sanitize_window(window: Any) -> dict[str, int | float] | None:
    if not isinstance(window, dict):
        return None
    cleaned: dict[str, int | float] = {}
    for key in ("usedPercent", "windowDurationMins", "resetsAt"):
        val = window.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) and val >= 0:
            cleaned[key] = val
    return cleaned if cleaned else None


def _sanitize_rate(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}

    rate_limits_obj = payload.get("rateLimits")
    source = rate_limits_obj if isinstance(rate_limits_obj, dict) else payload

    for key in ("primary", "secondary"):
        win = _sanitize_window(source.get(key))
        if win is not None:
            out[key] = win

    by_id = payload.get("rateLimitsByLimitId")
    if not isinstance(by_id, dict) and isinstance(rate_limits_obj, dict):
        by_id = rate_limits_obj.get("rateLimitsByLimitId")

    if isinstance(by_id, dict):
        limits: dict[str, Any] = {}
        for lid, value in list(by_id.items())[:32]:
            if isinstance(lid, str) and isinstance(value, dict):
                bucket_out: dict[str, Any] = {}
                for key in ("primary", "secondary"):
                    win = _sanitize_window(value.get(key))
                    if win is not None:
                        bucket_out[key] = win
                if bucket_out:
                    limits[lid[:80]] = bucket_out
        if limits:
            out["rateLimitsByLimitId"] = limits

    return out


def _run(
    argv: list[str],
    meta: dict[str, Any],
    timeout: float,
    cancel_event: threading.Event,
    *,
    model_listing: bool = False,
) -> dict[str, Any]:
    home = meta.get("home") or meta.get("config_dir")
    wsl_child = os.name == "nt" and Path(argv[0]).name.lower() in ("wsl.exe", "wsl")
    if not home and wsl_child:
        # _argv binds the explicit WSL account after resolving its own HOME.
        home = next((arg.partition("=")[2] for arg in argv if arg.startswith("CODEX_HOME=")), None)
    if not home:
        return {"status": "unconfigured", "error_code": "account_home_missing"}

    home_path = Path(str(home))
    if not wsl_child and (not home_path.is_absolute() or not home_path.is_dir()):
        return {"status": "unconfigured", "error_code": "account_home_invalid"}

    env = _clean_env(meta, str(home))

    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": env,
        "cwd": None if wsl_child else str(home),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    if cancel_event.is_set():
        return {"status": "error", "error_code": "cancelled"}

    try:
        proc = subprocess.Popen(argv, **kwargs)
    except OSError:
        return {"status": "error", "error_code": "launch_failed"}

    with _ACTIVE_LOCK:
        _ACTIVE[cancel_event] = proc

    if cancel_event.is_set():
        _terminate(proc)

    stderr_size = 0
    overflow = threading.Event()

    def drain_stderr() -> None:
        nonlocal stderr_size
        try:
            assert proc.stderr is not None
            while True:
                block = proc.stderr.read(8192)
                if not block:
                    break
                stderr_size += len(block)
                if stderr_size > MAX_OUTPUT:
                    overflow.set()
                    _terminate(proc)
                    break
        except Exception:
            pass

    thread = threading.Thread(target=drain_stderr, daemon=True)
    thread.start()

    timed_out = threading.Event()

    def timeout_kill() -> None:
        timed_out.set()
        _terminate(proc)

    timer = threading.Timer(timeout, timeout_kill)
    timer.daemon = True
    timer.start()

    def rpc() -> dict[str, Any]:
        stdout_size = 0
        assert proc.stdin is not None
        assert proc.stdout is not None

        def send(obj: dict[str, Any]) -> None:
            assert proc.stdin is not None
            data = json.dumps(obj) + "\n"
            proc.stdin.write(data.encode("utf-8"))
            proc.stdin.flush()

        send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "harness-farm-usage", "version": "1"},
                    "capabilities": {},
                },
            }
        )

        init: dict[str, Any] | None = None
        while True:
            if cancel_event.is_set():
                return {"status": "error", "error_code": "cancelled"}
            if overflow.is_set():
                return {"status": "error", "error_code": "output_limit"}
            if timed_out.is_set():
                return {"status": "error", "error_code": "timeout"}

            line = proc.stdout.readline(MAX_OUTPUT + 1)
            if not line:
                break
            stdout_size += len(line)
            if stdout_size > MAX_OUTPUT or len(line) > MAX_OUTPUT:
                overflow.set()
                _terminate(proc)
                return {"status": "error", "error_code": "output_limit"}

            try:
                msg = json.loads(line)
            except ValueError:
                continue

            if isinstance(msg, dict) and msg.get("id") == 1:
                init = msg
                break

        if overflow.is_set():
            return {"status": "error", "error_code": "output_limit"}
        if timed_out.is_set():
            return {"status": "error", "error_code": "timeout"}
        if cancel_event.is_set():
            return {"status": "error", "error_code": "cancelled"}
        if not isinstance(init, dict) or "error" in init:
            return {"status": "error", "error_code": "initialize_failed"}

        send({"jsonrpc": "2.0", "method": "initialized"})
        if model_listing:
            models: list[str] = []
            cursor: str | None = None
            seen_cursors: set[str] = set()
            for page in range(25):
                request_id = 10 + page
                send(
                    {
                        "id": request_id,
                        "method": "model/list",
                        "params": {
                            "limit": 100,
                            "includeHidden": False,
                            "cursor": cursor,
                        },
                    }
                )
                while True:
                    if timed_out.is_set() or cancel_event.is_set():
                        return {"status": "error", "error_code": "timeout"}
                    line = proc.stdout.readline(MAX_OUTPUT + 1)
                    stdout_size += len(line)
                    if not line or stdout_size > MAX_OUTPUT or overflow.is_set():
                        return {"status": "error", "error_code": "protocol_io"}
                    try:
                        reply = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(reply, dict) and reply.get("id") == request_id:
                        break
                payload = reply.get("result")
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    return {"status": "unsupported", "error_code": "model_list_failed"}
                for item in payload["data"]:
                    model = item.get("model") if isinstance(item, dict) else None
                    if isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model):
                        models.append(model)
                cursor = payload.get("nextCursor")
                if cursor is None:
                    return {
                        "status": "ok",
                        "models": list(dict.fromkeys(models)),
                        "source": "codex_app_server_model_list",
                        "access": "unknown",
                    }
                if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
            return {"status": "error", "error_code": "model_pagination_limit"}
        send({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}})
        send({"jsonrpc": "2.0", "id": 3, "method": "account/usage/read", "params": {}})

        replies: dict[int, Any] = {}
        while True:
            if cancel_event.is_set():
                return {"status": "error", "error_code": "cancelled"}
            if overflow.is_set():
                return {"status": "error", "error_code": "output_limit"}
            if timed_out.is_set():
                return {"status": "error", "error_code": "timeout"}

            if 2 in replies and 3 in replies:
                break

            line = proc.stdout.readline(MAX_OUTPUT + 1)
            if not line:
                break
            stdout_size += len(line)
            if stdout_size > MAX_OUTPUT or len(line) > MAX_OUTPUT:
                overflow.set()
                _terminate(proc)
                return {"status": "error", "error_code": "output_limit"}

            try:
                msg = json.loads(line)
            except ValueError:
                continue

            if isinstance(msg, dict):
                msg_id = msg.get("id")
                if msg_id in (2, 3) and msg_id not in replies:
                    replies[msg_id] = msg
                    if msg_id == 2 and "error" in msg:
                        return {"status": "error", "error_code": "rate_limits_failed"}

        if overflow.is_set():
            return {"status": "error", "error_code": "output_limit"}
        if timed_out.is_set():
            return {"status": "error", "error_code": "timeout"}
        if cancel_event.is_set():
            return {"status": "error", "error_code": "cancelled"}

        rate = replies.get(2)
        if rate is None:
            return {"status": "error", "error_code": "protocol_io"}
        if not isinstance(rate, dict) or "error" in rate:
            return {"status": "error", "error_code": "rate_limits_failed"}

        result = rate.get("result")
        if not isinstance(result, dict):
            return {"status": "error", "error_code": "rate_limits_failed"}

        clean: dict[str, Any] = {
            "status": "ok",
            "rate_limits": _sanitize_rate(result),
        }

        usage = replies.get(3)
        if isinstance(usage, dict) and isinstance(usage.get("error"), dict):
            clean["daily_usage_status"] = "unsupported" if usage["error"].get("code") == -32601 else "unknown"
            clean["daily_usage_buckets"] = []
        elif isinstance(usage, dict) and isinstance(usage.get("result"), dict):
            buckets = usage["result"].get("dailyUsageBuckets")
            if isinstance(buckets, list):
                clean["daily_usage_status"] = "ok"
                valid_buckets = []
                for b in buckets[:100]:
                    if not isinstance(b, dict):
                        continue
                    start_date = b.get("startDate")
                    tokens = b.get("tokens")
                    if (
                        _is_valid_date(start_date)
                        and isinstance(tokens, int)
                        and not isinstance(tokens, bool)
                        and tokens >= 0
                    ):
                        valid_buckets.append({"startDate": start_date, "tokens": tokens})
                clean["daily_usage_buckets"] = valid_buckets
            else:
                clean["daily_usage_status"] = "unknown"
        else:
            clean["daily_usage_status"] = "unknown"

        return clean

    try:
        outcome = rpc()
        if overflow.is_set():
            return {"status": "error", "error_code": "output_limit"}
        if timed_out.is_set():
            return {"status": "error", "error_code": "timeout"}
        return outcome
    except Exception:
        if overflow.is_set():
            return {"status": "error", "error_code": "output_limit"}
        if timed_out.is_set():
            return {"status": "error", "error_code": "timeout"}
        return {"status": "error", "error_code": "protocol_io"}
    finally:
        timer.cancel()
        _terminate(proc)
        _reap(proc)
        for s in (proc.stdin, proc.stdout, proc.stderr):
            if s is not None and not s.closed:
                try:
                    s.close()
                except Exception:
                    pass
        thread.join(timeout=1.0)
        with _ACTIVE_LOCK:
            _ACTIVE.pop(cancel_event, None)


async def probe_codex(
    meta: dict[str, Any], timeout: float = TIMEOUT_S, *, model_listing: bool = False
) -> dict[str, Any]:
    """Probe configured account only; process work is moved off selector loops used on Windows."""
    prefix = meta.get("cli_prefix")
    named_wsl = (
        os.name == "nt"
        and isinstance(prefix, list)
        and len(prefix) >= 7
        and Path(str(prefix[0])).name.lower() in ("wsl.exe", "wsl")
        and prefix[1] == "-d"
        and prefix[3] == "-u"
    )
    if not (meta.get("home") or meta.get("config_dir") or named_wsl):
        return {"status": "unconfigured", "error_code": "account_home_missing"}

    try:
        argv = await asyncio.wait_for(asyncio.to_thread(_argv, meta), timeout=9.0)
    except TimeoutError:
        argv = None
    if not argv:
        prefix = meta.get("cli_prefix")
        if prefix is not None:
            return {"status": "unsupported", "error_code": "cli_prefix_unsupported"}
        return {"status": "unsupported", "error_code": "codex_cli_missing"}

    cancel_event = threading.Event()
    worker = asyncio.create_task(
        asyncio.to_thread(
            _run,
            argv,
            meta,
            min(float(timeout), TIMEOUT_S),
            cancel_event,
            **({"model_listing": True} if model_listing else {}),
        )
    )
    try:
        return await asyncio.wait_for(asyncio.shield(worker), timeout=TIMEOUT_S + 2.0)
    except (asyncio.CancelledError, TimeoutError):
        cancel_event.set()
        with _ACTIVE_LOCK:
            proc = _ACTIVE.get(cancel_event)
        if proc is not None:
            await asyncio.to_thread(_terminate, proc)
        try:
            await asyncio.wait_for(asyncio.shield(worker), timeout=3.0)
        except Exception:
            pass
        raise
