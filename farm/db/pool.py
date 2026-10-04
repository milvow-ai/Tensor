"""Database URL resolution and the shared async connection pool.

``get_db_url()`` order (CONTEXT section 3): ``FARM_DB_URL`` -> ``SUPABASE_DB_URL`` -> local embedded Postgres
(``farm.db.local``, started on demand, trust auth, so the URL carries no password).

Event loop caveat (Windows): psycopg's async mode cannot run on the default ProactorEventLoop, and the
selector loop it needs cannot start asyncio subprocesses. No global policy is changed here (that would break
every ``asyncio.create_subprocess_exec`` in the process); code that opens the pool runs on ``loop_factory``
(``run(main())`` does that), and code that must also spawn subprocesses on Windows has to do so through a
thread (``asyncio.to_thread(subprocess.run, ...)``).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from typing import Any

from psycopg import AsyncConnection
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from farm.settings import load_env

loop_factory = asyncio.SelectorEventLoop
"""Event loop factory psycopg async works with on every platform (the default loop on Linux and macOS)."""


def run[T](main: Coroutine[Any, Any, T]) -> T:
    """``asyncio.run`` on a selector loop: the entry point for any process that opens the pool."""
    return asyncio.run(main, loop_factory=loop_factory)


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", ""}

type DbPool = AsyncConnectionPool[AsyncConnection[TupleRow]]
"""The pool type used across the Farm (annotate ``pool: DbPool``)."""

_global_pool: DbPool | None = None


def _env_url() -> str | None:
    load_env()
    for name in ("FARM_DB_URL", "SUPABASE_DB_URL"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def get_db_url(force_local: bool = False) -> str:
    """Return the database URL: FARM_DB_URL, else SUPABASE_DB_URL, else the embedded local Postgres.

    ``force_local=True`` skips the environment and always returns the embedded server (used by
    ``farm db ... --local``).
    """
    url = None if force_local else _env_url()
    if url:
        return url
    from farm.db.local import start_local

    return start_local()


def is_local_url(url: str) -> bool:
    """True when the URL's host is loopback or a unix socket (parsed, never a substring match)."""
    try:
        info = conninfo_to_dict(url)
    except Exception:
        return False
    hosts = str(info.get("host") or info.get("hostaddr") or "")
    return all(h.strip() in _LOOPBACK_HOSTS or h.strip().startswith("/") for h in hosts.split(","))


def is_local_db() -> bool:
    """True when the configured target (see ``get_db_url``) is the embedded or a loopback database."""
    url = _env_url()
    return url is None or is_local_url(url)


async def open_pool(db_url: str | None = None) -> DbPool:
    """Open an ``AsyncConnectionPool`` (min 1, max 10, autocommit).

    Without ``db_url`` the pool is the process-wide one (reused until ``close_pool``). With an explicit URL a
    separate pool is returned that the caller closes itself.
    """
    global _global_pool
    if db_url is None and _global_pool is not None and not _global_pool.closed:
        return _global_pool
    url = db_url or get_db_url()
    pool: DbPool = AsyncConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=10,
        # prepare_threshold=None: stay compatible with transaction-mode poolers (Supabase Supavisor).
        kwargs={"autocommit": True, "prepare_threshold": None},
        open=False,
    )
    await pool.open(wait=True, timeout=30)
    if db_url is None:
        _global_pool = pool
    return pool


async def close_pool() -> None:
    """Close the process-wide pool if it is open."""
    global _global_pool
    pool, _global_pool = _global_pool, None
    if pool is not None:
        await pool.close()
