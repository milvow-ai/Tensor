"""Embedded local PostgreSQL (pgserver) for development and tests.

Local mode is used when neither ``FARM_DB_URL`` nor ``SUPABASE_DB_URL`` is set. The server keeps running
between processes (``cleanup_mode=None``) and every function here is idempotent.

Crash recovery (why this module does more than call ``pgserver.get_server``). Every case below was hit or is
reproduced in ``tests/test_db_schema.py``:

* **Server log outside the data directory (Windows).** pgserver starts postgres with ``-l <pgdata>/log``.
  After an unclean stop (killed process, power loss, PC shutdown) the next start runs crash recovery, which
  fsyncs every file under the data directory (``SyncDataDirectory``; skipped only when ``fsync = off``). The
  *new* postmaster itself holds ``<pgdata>/log`` open (its stdout/stderr redirect is opened without write
  sharing), so the startup process cannot open it: ``could not open file "./log": sharing violation``,
  retried for 30 s. pgserver's ``pg_ctl`` gives up after 10 s (``TimeoutExpired``) and leaves a half-started
  postmaster behind, so a durable (``fsync = on``) database could not restart after a hard kill. Neither
  killing leftovers nor waiting helps: the holder is the server being started. The cure is not to put the log
  inside the data directory: on Windows this module starts the postmaster itself with
  ``pg_ctl -l <FARM_DATA_DIR>/logs/<name>.log`` and lets pgserver attach to the running server. Crash
  recovery then works with ``fsync = on`` (the owner's runtime database) and ``fsync = off`` (throwaway test
  databases, where the skipped fsyncs only make tests faster). Reproduced and guarded by
  ``tests/test_db_schema.py::test_local_server_restarts_after_a_hard_kill`` (both modes).
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
import sys
import tempfile
import time
from pathlib import Path

import psutil
import structlog
from pgserver._commands import POSTGRES_BIN_PATH
from pgserver.postgres_server import PostgresServer, get_server
from pgserver.utils import find_suitable_port

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
_START_TIMEOUT_S = 180  # crash recovery of a durable cluster fsyncs every file
_LISTEN_HOST = "127.0.0.1"


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


def _log_path(pgdata: Path) -> Path:
    """Server log of ``pgdata``: next to it (``<FARM_DATA_DIR>/logs/<name>.log``), never inside it."""
    return pgdata.parent / "logs" / f"{pgdata.name}.log"


def _log_tail(pgdata: Path, lines: int = 12) -> str:
    try:
        text = _log_path(pgdata).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no server log)"
    return "\n".join(text.splitlines()[-lines:])


def _prepare_log(pgdata: Path) -> Path:
    """Create the log directory, move a legacy ``<pgdata>/log`` out of the data directory, cap the size.

    Only called while the server is down, so the files are free.
    """
    logfile = _log_path(pgdata)
    logfile.parent.mkdir(parents=True, exist_ok=True)
    legacy = pgdata / "log"
    try:
        if legacy.is_file():
            target = logfile.with_name(f"{pgdata.name}.legacy.log")
            shutil.move(str(legacy), str(target))
            log.info("local_pg.legacy_log_moved", pgdata=str(pgdata), to=str(target))
        for candidate in (logfile, logfile.with_name(f"{pgdata.name}.legacy.log")):
            if candidate.exists() and candidate.stat().st_size > _MAX_LOG_BYTES:
                candidate.write_text("", encoding="utf-8")
                log.info("local_pg.server_log_truncated", path=str(candidate))
    except OSError as exc:
        log.warning("local_pg.server_log_prepare_failed", pgdata=str(pgdata), error=exc.__class__.__name__)
    return logfile


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
    """Run a bundled PostgreSQL executable (argv list, killed when the timeout expires).

    Output goes to temporary files, not pipes: ``pg_ctl start`` leaves a daemon that inherits the pipes and
    ``subprocess.run`` would wait for them to close.
    """
    with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
        done = subprocess.run(
            [str(POSTGRES_BIN_PATH / tool), *args],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            text=True,
            timeout=timeout,
        )
        out.seek(0)
        err.seek(0)
        detail = (err.read() or out.read()).strip()[-400:]
    if done.returncode != 0:
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
    """Bring ``target`` into a state pgserver can start from, whatever a crash left behind.

    Callers hold pgserver's inter-process lock, so no two processes initialise or start at once.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
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


def _start_postmaster(target: Path) -> None:
    """Start postgres with its log outside the data directory (see the module docstring), wait until ready."""
    logfile = _prepare_log(target)
    port = find_suitable_port(_LISTEN_HOST)
    try:
        _pg_ctl(
            target,
            "-w",
            "-t",
            str(_START_TIMEOUT_S),
            "-o",
            f'-h "{_LISTEN_HOST}"',
            "-o",
            f"-p {port}",
            "-l",
            str(logfile),
            "start",
            timeout=_START_TIMEOUT_S + 30,
        )
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        reason = exc.__class__.__name__
        raise RuntimeError(
            f"local postgres failed to start ({reason}); server log tail:\n{_log_tail(target)}"
        ) from None
    _wait_ready(target)
    log.info("local_pg.started", pgdata=str(target), port=port, log=str(logfile))


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

    with PostgresServer._lock:  # pgserver's inter-process lock: one process initialises/starts at a time
        running = _postmaster_alive(target)
        if running:
            _wait_ready(target)
            running = _postmaster_alive(target)
        if not running:
            _clear_stale_pid(target)
        _prepare_pgdata(target)
        conf_changed = _apply_local_conf(target, durable=durable)
        if not running and sys.platform == "win32":
            _start_postmaster(target)
        # elsewhere pgserver starts the server itself below (no log/crash-recovery clash outside Windows)

    server = get_server(target, cleanup_mode=None)  # attaches to the running server (or starts it)

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
