"""Embedded local PostgreSQL (pgserver) for development and tests.

Local mode is used when neither ``FARM_DB_URL`` nor ``SUPABASE_DB_URL`` is set. The server keeps running
between processes (``cleanup_mode=None``) and every function here is idempotent.

Crash recovery (why this module does more than call ``pgserver.get_server``). Every case below was hit or is
reproduced in ``tests/test_db_schema.py``:

* pgserver starts postgres with ``-l <pgdata>/log``, so the log file lives inside the data directory and is
  held open by the server. After an unclean stop (killed process, power loss, PC shutdown) the next start runs
  crash recovery, which fsyncs every file in the data directory. On Windows opening the held ``log`` fails
  with a sharing violation that PostgreSQL retries for 30 s, longer than pgserver's 10 s ``pg_ctl`` timeout,
  so startup fails with ``TimeoutExpired`` and leaves a half-started postmaster behind. ``fsync = off`` skips
  that directory sync (``SyncDataDirectory`` returns early). A killed *process* loses no committed data with
  it (commits still reach the OS); only an OS crash or power loss could corrupt this dev/test database, whose
  durable counterpart is Supabase. The setting lives in a managed block of ``postgresql.conf``.
* A ``postmaster.pid`` left by a dead server (or by a torn write, or naming a recycled PID) is removed.
* A server that is still starting (``status`` not yet ``ready``) is waited for instead of asserted on.
* pgserver's ``.handle_pids.json`` is rewritten when a crash left it empty or corrupt (it would otherwise
  raise ``JSONDecodeError`` on every start) and pruned of dead PIDs.
* A new cluster is created by ``initdb`` into a sibling ``<pgdata>.init-<pid>`` directory that is renamed
  into place when complete, so an interrupted ``initdb`` never leaves a half-initialised ``pgdata``. Leftover
  staging directories and in-place debris without ``global/pg_control`` (no cluster, so no data) are removed.
* The cached ``PostgresServer`` handle is dropped when its postmaster is gone, so a stop/reset followed by a
  start in the same process really restarts it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import psutil
import structlog
from pgserver._commands import POSTGRES_BIN_PATH
from pgserver.postgres_server import PostgresServer, get_server

from farm.settings import data_dir

log = structlog.get_logger(__name__)

_CONF_BEGIN = "# >>> farm-local (managed by farm.db.local, do not edit) >>>"
_CONF_END = "# <<< farm-local <<<"
_CONF_SETTINGS_DURABLE = ("fsync = on",)
_CONF_SETTINGS_EPHEMERAL = ("fsync = off",)
_CONF_RE = re.compile(re.escape(_CONF_BEGIN) + r".*?" + re.escape(_CONF_END) + r"\n?", re.DOTALL)


def _conf_block(durable: bool) -> str:
    settings = _CONF_SETTINGS_DURABLE if durable else _CONF_SETTINGS_EPHEMERAL
    return "\n".join([_CONF_BEGIN, *settings, _CONF_END]) + "\n"


_INITDB_ARGS = ("--auth=trust", "--auth-local=trust", "--encoding=utf8", "-U", "postgres")  # as pgserver
_MAX_LOG_BYTES = 8 * 1024 * 1024
_READY_TIMEOUT_S = 90.0


def get_local_pgdata(pgdata: Path | None = None) -> Path:
    """Return the resolved pgdata directory, default ``<FARM_DATA_DIR>/pg``."""
    if pgdata is not None:
        return pgdata.expanduser().resolve()
    return (data_dir() / "pg").resolve()


def _same_path(a: str, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(str(b)))


def _pid_file_lines(pgdata: Path) -> list[str]:
    try:
        return (pgdata / "postmaster.pid").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _postmaster_alive(pgdata: Path) -> bool:
    """True when ``postmaster.pid`` names a live postgres process that serves this pgdata.

    Conservative on purpose: a wrong "dead" answer would make us delete the pid file of a live server, so only
    a missing process, a non-postgres process (recycled PID) or a postgres started with a different ``-D``
    count as stale.
    """
    lines = _pid_file_lines(pgdata)
    if not lines:
        return False
    try:
        proc = psutil.Process(int(lines[0].strip()))
        if not proc.name().lower().startswith("postgres"):
            return False
        try:
            cmdline = proc.cmdline()
        except psutil.AccessDenied:
            return True
    except (ValueError, psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    for flag, value in zip(cmdline, cmdline[1:], strict=False):
        if flag == "-D":
            return _same_path(value, pgdata)
    return True


def _status(pgdata: Path) -> str:
    lines = _pid_file_lines(pgdata)
    return lines[7].strip() if len(lines) >= 8 else ""


def _clear_stale_pid(pgdata: Path) -> None:
    pid_file = pgdata / "postmaster.pid"
    if pid_file.exists() and not _postmaster_alive(pgdata):
        pid = (_pid_file_lines(pgdata) or ["?"])[0]
        log.warning("local_pg.stale_pid_file_removed", pgdata=str(pgdata), pid=pid)
        pid_file.unlink(missing_ok=True)


def _repair_handle_list(pgdata: Path) -> None:
    """Keep pgserver's ``.handle_pids.json`` parseable (a crash can leave it empty) and free of dead PIDs."""
    path = pgdata / ".handle_pids.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        valid = isinstance(data, list) and all(isinstance(pid, int) for pid in data)
    except (ValueError, OSError):
        valid = False
        data = []
    if not valid:
        log.warning("local_pg.handle_list_reset", pgdata=str(pgdata))
        path.write_text("[]", encoding="utf-8")
        return
    live = [pid for pid in data if psutil.pid_exists(pid)]
    if live != data:
        path.write_text(json.dumps(live), encoding="utf-8")


def _rotate_log(pgdata: Path) -> None:
    """Truncate an oversized server log (only called while the server is down, so the file is free)."""
    log_file = pgdata / "log"
    try:
        if log_file.exists() and log_file.stat().st_size > _MAX_LOG_BYTES:
            log_file.write_text("", encoding="utf-8")
            log.info("local_pg.server_log_truncated", pgdata=str(pgdata))
    except OSError as exc:
        log.warning("local_pg.server_log_truncate_failed", pgdata=str(pgdata), error=exc.__class__.__name__)


def _is_runtime_dir(target: Path) -> bool:
    try:
        return _same_path(str(target), data_dir() / "pg")
    except Exception:
        return False


def _is_test_dir(target: Path) -> bool:
    return not _is_runtime_dir(target)


def _apply_local_conf(pgdata: Path, *, durable: bool | None = None) -> bool:
    """Make sure ``postgresql.conf`` ends with the managed settings block. Returns True when it changed."""
    conf = pgdata / "postgresql.conf"
    if not conf.exists():
        return False
    target = pgdata.expanduser().resolve()
    if _is_runtime_dir(target):
        # Never disable durability for the owner's runtime data dir
        effective_durable = True
    elif durable is not None:
        effective_durable = durable
    else:
        effective_durable = not _is_test_dir(target)

    block = _conf_block(effective_durable)
    text = conf.read_text(encoding="utf-8")
    if _CONF_RE.search(text):
        new = _CONF_RE.sub(lambda _m: block, text, count=1)
    else:
        new = text if text.endswith("\n") or not text else text + "\n"
        new += block
    if new == text:
        return False
    conf.write_text(new, encoding="utf-8", newline="\n")
    settings = _CONF_SETTINGS_DURABLE if effective_durable else _CONF_SETTINGS_EPHEMERAL
    log.info("local_pg.conf_updated", pgdata=str(pgdata), settings=list(settings))
    return True


def _run_tool(tool: str, *args: str, timeout: float) -> None:
    """Run a bundled PostgreSQL executable (argv list, captured output, killed when the timeout expires)."""
    done = subprocess.run(
        [str(POSTGRES_BIN_PATH / tool), *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[-400:]
        raise RuntimeError(f"{tool} {args[-1]} failed (exit {done.returncode}): {detail}")


def _pg_ctl(pgdata: Path, *args: str, timeout: float = 60.0) -> None:
    _run_tool("pg_ctl", "-D", str(pgdata), *args, timeout=timeout)


def _rmtree(path: Path, timeout: float = 30.0) -> None:
    """Delete a directory tree; Windows may keep files locked for a moment after a process exits."""
    deadline = time.monotonic() + timeout
    while path.exists():
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"could not delete {path}; is another process still using it?")
        time.sleep(0.5)


def _initialise(target: Path) -> None:
    """Create a fresh cluster at ``target`` atomically: initdb into a sibling directory, then rename it."""
    staging = target.with_name(f"{target.name}.init-{os.getpid()}")
    _rmtree(staging)
    try:
        _run_tool("initdb", "-D", str(staging), *_INITDB_ARGS, timeout=180.0)
        _rmtree(target)  # empty (or debris) directory left in the way
        deadline = time.monotonic() + 15.0
        while True:
            try:
                staging.rename(target)
                break
            except PermissionError:  # Windows: a scanner may still hold a file of the fresh cluster
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
    except BaseException:
        _rmtree(staging)
        raise
    log.info("local_pg.cluster_initialised", pgdata=str(target))


def _prepare_pgdata(target: Path) -> None:
    """Bring ``target`` into a state pgserver can start from, whatever a crash left behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with PostgresServer._lock:  # pgserver's own inter-process lock: no two processes initialise at once
        for debris in target.parent.glob(f"{target.name}.init-*"):
            log.warning("local_pg.interrupted_initdb_removed", path=str(debris))
            _rmtree(debris)
        initialised = (target / "PG_VERSION").exists() and (target / "global" / "pg_control").exists()
        if not initialised:
            if target.exists() and any(target.iterdir()):
                if not any((target / marker).exists() for marker in ("PG_VERSION", "base", "global")):
                    raise RuntimeError(f"{target} is not empty and is not a PostgreSQL data directory")
                log.warning("local_pg.incomplete_cluster_discarded", pgdata=str(target))
                _rmtree(target)
            _initialise(target)
        _repair_handle_list(target)


def _wait_ready(pgdata: Path, timeout: float = _READY_TIMEOUT_S) -> None:
    """Wait for an already running postmaster that is still starting (crash recovery) to become ready."""
    deadline = time.monotonic() + timeout
    if _status(pgdata) != "ready":
        log.info("local_pg.waiting_for_ready", pgdata=str(pgdata), status=_status(pgdata) or "starting")
    while _status(pgdata) != "ready":
        if not _postmaster_alive(pgdata):
            return  # died while starting: let pgserver start a fresh one
        if time.monotonic() > deadline:
            raise TimeoutError(f"local postgres at {pgdata} did not become ready in {timeout:.0f}s")
        time.sleep(0.5)


def get_local_server(
    pgdata: Path | None = None,
    *,
    durable: bool | None = None,
) -> PostgresServer:
    """Get the embedded PostgreSQL server for ``pgdata``, starting (and initialising) it when needed."""
    target = get_local_pgdata(pgdata)

    # pgserver caches handles per pgdata in a class attribute (it has no public API to evict one).
    if target in PostgresServer._instances and not _postmaster_alive(target):
        PostgresServer._instances.pop(target, None)  # server died or was stopped behind our back

    running = _postmaster_alive(target)
    if running:
        _wait_ready(target)
        running = _postmaster_alive(target)
    if not running:
        _clear_stale_pid(target)
    _prepare_pgdata(target)
    if not running:
        _rotate_log(target)
    conf_changed = _apply_local_conf(target, durable=durable)

    server = get_server(target, cleanup_mode=None)

    if conf_changed and running:  # a live server picks the new settings up on reload
        _pg_ctl(target, "reload")
    return server


def get_local_port(pgdata: Path | None = None, *, durable: bool | None = None) -> int:
    """Port of the running embedded server (starts it if needed)."""
    port = get_local_server(pgdata, durable=durable).get_postmaster_info().port
    if port is None:
        raise RuntimeError("local postgres exposes no TCP port")
    return int(port)


def start_local(
    pgdata: Path | None = None,
    database: str = "postgres",
    *,
    durable: bool | None = None,
) -> str:
    """Start (or reuse) the embedded server; return a URI for ``database`` (trust auth: no password)."""
    return str(get_local_server(pgdata, durable=durable).get_uri(database=database))


def stop_local(pgdata: Path | None = None) -> bool:
    """Stop the embedded server cleanly. Returns True when a running server was stopped."""
    target = get_local_pgdata(pgdata)
    PostgresServer._instances.pop(target, None)
    if not target.exists():
        return False
    if not _postmaster_alive(target):
        _clear_stale_pid(target)
        return False
    _pg_ctl(target, "-w", "-t", "60", "-m", "fast", "stop", timeout=90.0)
    return True


def reset_local(
    pgdata: Path | None = None,
    database: str = "postgres",
    *,
    durable: bool | None = None,
) -> str:
    """Stop the embedded server, delete its data directory and start a fresh one."""
    target = get_local_pgdata(pgdata)
    if target.exists() and any(target.iterdir()) and not (target / "PG_VERSION").exists():
        raise RuntimeError(f"{target} is not a PostgreSQL data directory; refusing to delete it")
    stop_local(target)
    _rmtree(target)
    return start_local(pgdata=target, database=database, durable=durable)
