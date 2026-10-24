"""A sign-in CLI as a child process: its output as async lines, text typed into its stdin, a hard kill.

The Farm runs on a selector event loop (psycopg), which cannot start subprocesses on Windows, so this uses
``subprocess.Popen`` with a reader thread, the way ``farm/executors/cli_agent/base.py`` runs agent CLIs.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from farm.executors.cli_agent.base import _kill_process_tree, resolve_shim
from farm.secrets import redact

_EOF = object()


def resolve_cli(name: str) -> list[str]:
    """The argv prefix that runs ``name`` (npm ``.cmd`` shims resolved to their real program)."""
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(f"the '{name}' CLI is not installed on the machine that runs the Farm")
    prefix, _ = resolve_shim(path)
    return list(prefix)


def child_env(overrides: Mapping[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(overrides)
    env["NO_COLOR"] = "1"
    return env


class CliProcess:
    """One running sign-in command. ``lines()`` yields its output; ``transcript`` keeps what was read."""

    def __init__(self, argv: Sequence[str], env: Mapping[str, str], cwd: Path | None = None) -> None:
        self._argv = list(argv)
        self._env = dict(env)
        self._cwd = cwd
        self._proc: subprocess.Popen[bytes] | None = None
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self.transcript: list[str] = []

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        extra: dict[str, Any] = {} if sys.platform == "win32" else {"start_new_session": True}
        proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self._cwd,
            env=self._env,
            **extra,
        )
        self._proc = proc

        def put(item: Any) -> None:
            try:
                loop.call_soon_threadsafe(self._queue.put_nowait, item)
            except RuntimeError:  # the loop is gone (Farm shutting down)
                pass

        def pump() -> None:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
            put(_EOF)

        threading.Thread(target=pump, daemon=True, name="connect-cli-reader").start()

    async def lines(self) -> AsyncIterator[str]:
        while True:
            item = await self._queue.get()
            if item is _EOF:
                return
            line = str(item)
            self.transcript.append(line)
            yield line

    async def write(self, text: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise RuntimeError("the sign-in program is not running any more")
        stdin = proc.stdin
        data = (text.strip() + "\n").encode("utf-8")

        def feed() -> None:
            stdin.write(data)
            stdin.flush()

        await asyncio.to_thread(feed)

    async def wait(self) -> int:
        proc = self._proc
        if proc is None:
            return -1
        return await asyncio.to_thread(proc.wait)

    def kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)

    def excerpt(self, last: int = 6) -> str:
        return redact("\n".join(line for line in self.transcript[-last:] if line.strip()))


async def run_once(
    argv: Sequence[str], env: Mapping[str, str], cwd: Path | None, timeout_s: float
) -> tuple[int, str]:
    """Run a short command to the end (status, test prompt); (-1, output so far) on timeout."""

    def run() -> tuple[int, str]:
        try:
            done = subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=dict(env),
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raw = exc.stdout if isinstance(exc.stdout, bytes) else b""
            return -1, raw.decode("utf-8", errors="replace")
        return done.returncode, done.stdout.decode("utf-8", errors="replace")

    return await asyncio.to_thread(run)


def replace_files(staging: Path, home: Path, names: Sequence[str]) -> None:
    """Copy the login files ``names`` from ``staging`` into an existing ``home``, each replaced atomically.

    Everything else in ``home`` (settings, history, sessions) stays; the old file is kept as ``<name>.prev``.
    """
    home.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = staging / name
        if not source.exists():
            continue
        target = home / name
        fresh = home / f".{name}.farm-new"
        shutil.copy2(source, fresh)
        if target.exists():
            shutil.copy2(target, home / f"{name}.prev")
        os.replace(fresh, target)
