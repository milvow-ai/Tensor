"""Shared fixtures (M1a): throw-away migrated Postgres for tests.

* ``db_url``  (session) - embedded pgserver in ``<FARM_DATA_DIR>/pgtest``; a fresh database
  ``farm_test_<random>`` is created, migrated to head with Alembic, and dropped at the end of the session.
  The pgtest server itself is left running (other sessions may share it); the dev server in ``pg`` is untouched.
* ``pool``    (function) - async pool on that database; every test starts with all tables empty except
  ``alembic_version`` (kept) and ``farm_settings`` (singleton row reset to its defaults).
* ``registry``  (function) - ``tests/fixtures/registry.yaml`` loaded and validated.
* ``scratch_db`` (function) - factory for extra empty databases (migration round-trip, RLS tests).
* ``seed_connection(pool, provider_id, conn_id, units=...)`` - helper to insert a provider + connection + units.

Event loops: tests that use the database run on a selector loop (psycopg async needs one on Windows);
all other async tests keep the platform default loop (Windows: Proactor, which asyncio subprocesses need).
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
import pytest_asyncio
import respx
from alembic import command
from psycopg import sql
from psycopg.conninfo import make_conninfo

from farm.context import FarmContext
from farm.control.cli import alembic_config
from farm.db.local import get_local_server
from farm.db.pool import DbPool, loop_factory, open_pool
from farm.executors.api import ApiExecutor
from farm.registry import Registry, load_registry
from farm.registry.sync import sync_registry
from farm.settings import data_dir


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    """pytest-asyncio hook: which event loop each async test runs on."""
    if "db_url" in getattr(item, "fixturenames", ()):
        return {"selector": loop_factory}
    return {"default": asyncio.new_event_loop}


FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "registry.yaml"

_RESET_SETTINGS = """
update public.farm_settings
   set owner_email = null, global_monthly_budget_usd = null, alert_thresholds = '{50,80,100}', timezone = 'UTC'
 where id = 1
"""


def _create_database(admin_url: str) -> str:
    name = f"farm_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
    return name


def _drop_database(admin_url: str, name: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("drop database if exists {} with (force)").format(sql.Identifier(name)))


@pytest.fixture
def registry() -> Registry:
    return load_registry(FIXTURE_REGISTRY)


@pytest.fixture(scope="session")
def pg_admin_url() -> str:
    """URI of the maintenance database of the embedded test server."""
    return str(get_local_server(data_dir() / "pgtest").get_uri(database="postgres"))


@pytest.fixture(scope="session")
def db_url(pg_admin_url: str) -> Iterator[str]:
    """Session database, migrated to head."""
    name = _create_database(pg_admin_url)
    url = make_conninfo(pg_admin_url, dbname=name)
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        _drop_database(pg_admin_url, name)


@pytest.fixture
def scratch_db(pg_admin_url: str) -> Iterator[Callable[[], str]]:
    """Factory returning the URL of a new empty database (dropped after the test)."""
    created: list[str] = []

    def make() -> str:
        name = _create_database(pg_admin_url)
        created.append(name)
        return make_conninfo(pg_admin_url, dbname=name)

    yield make
    for name in created:
        _drop_database(pg_admin_url, name)


async def truncate_all(pool: DbPool) -> None:
    """Empty every public table except alembic_version and farm_settings; reset farm_settings."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select string_agg(format('%I.%I', schemaname, tablename), ', ') from pg_tables "
            "where schemaname = 'public' and tablename not in ('alembic_version', 'farm_settings')"
        )
        row = await cur.fetchone()
        if row and row[0]:
            await conn.execute(sql.SQL("truncate table {} restart identity cascade").format(sql.SQL(row[0])))
        await conn.execute(_RESET_SETTINGS)


@pytest_asyncio.fixture
async def pool(db_url: str) -> AsyncIterator[DbPool]:
    p = await open_pool(db_url)
    try:
        await truncate_all(p)
        yield p
    finally:
        await p.close()


async def seed_connection(
    pool: DbPool,
    provider_id: str,
    conn_id: str,
    units: Mapping[str, Mapping[str, Any] | int | float | None] | None = None,
    *,
    kind: str = "tool",
    executor: str = "api",
    **connection_fields: Any,
) -> None:
    """Insert (or reuse) a provider, a connection and its consumption units.

    ``units`` maps unit name -> either a plain limit (number, ``None`` = unlimited; period ``day``) or a dict
    with any of ``limit``, ``period`` (default ``day``), ``anchor``, ``charged_on`` (default ``attempt``),
    ``unit_cost_usd``, ``estimate_per_call``. ``connection_fields`` are extra ``connections`` columns
    (``priority``, ``status``, ``concurrency`` ...; ``plan`` / ``meta`` may be plain dicts).
    """
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.providers (id, name, kind, executor) values (%s, %s, %s, %s) "
            "on conflict (id) do nothing",
            (provider_id, provider_id, kind, executor),
        )
        columns = ["id", "provider_id", *connection_fields]
        values = [conn_id, provider_id, *(_json(v) for v in connection_fields.values())]
        await conn.execute(
            sql.SQL("insert into public.connections ({}) values ({}) on conflict (id) do nothing").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.SQL(", ").join(sql.Placeholder() * len(values)),
            ),
            values,
        )
        for unit, spec in (units or {}).items():
            cfg: Mapping[str, Any] = spec if isinstance(spec, Mapping) else {"limit": spec}
            await conn.execute(
                "insert into public.consumption_units "
                "(connection_id, unit, limit_value, period, reset_anchor, charged_on, unit_cost_usd, estimate_per_call) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (connection_id, unit) do update set limit_value = excluded.limit_value, "
                "period = excluded.period, reset_anchor = excluded.reset_anchor, charged_on = excluded.charged_on, "
                "unit_cost_usd = excluded.unit_cost_usd, estimate_per_call = excluded.estimate_per_call",
                (
                    conn_id,
                    unit,
                    cfg.get("limit"),
                    cfg.get("period", "day"),
                    cfg.get("anchor"),
                    cfg.get("charged_on", "attempt"),
                    cfg.get("unit_cost_usd", 0),
                    cfg.get("estimate_per_call", 1),
                ),
            )


def _json(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value) if isinstance(value, dict) else value


# --- M1c fixtures: a synced registry, a FarmContext, a movable clock, mocked providers ------------------------

REOON_URL = "https://emailverifier.reoon.com/api/v1/verify"
ZEROBOUNCE_URL = "https://api.zerobounce.net/v2/validate"
START = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class FakeClock:
    """An injectable clock (``FarmContext.clock``): cache expiry, cooldowns and leases are tested by moving it."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def reoon_body(status: str = "safe", email: str = "jane.doe@example.com") -> dict[str, Any]:
    return {"email": email, "status": status, "verification_mode": "power", "is_valid_syntax": True}


def zerobounce_body(status: str = "valid", email: str = "jane.doe@example.com") -> dict[str, Any]:
    return {"address": email, "status": status, "sub_status": ""}


@pytest.fixture(autouse=True)
def _owner_registry_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The self-serve commands rewrite the owner's registry file: no test may touch the real config/registry.yaml."""
    monkeypatch.setenv("FARM_REGISTRY_PATH", str(tmp_path / "owner-registry.yaml"))


@pytest.fixture
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The keys the fixture registry points at; the values are fake (no network in tests)."""
    for name in ("REOON_API_KEY", "REOON_API_KEY_2", "ZEROBOUNCE_API_KEY"):
        monkeypatch.setenv(name, f"test-key-{name.lower()}")


@pytest.fixture
def http() -> Iterator[respx.MockRouter]:
    """All HTTP is mocked: a request without a route fails the test instead of leaving the machine."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest_asyncio.fixture
async def farm_factory(
    pool: DbPool, registry: Registry, provider_keys: None
) -> AsyncIterator[Callable[..., Awaitable[FarmContext]]]:
    """``await make(registry=None, **context_fields)``: sync a registry (default: the fixture one) and return a context."""
    contexts: list[FarmContext] = []

    async def make(reg: Registry | None = None, **fields: Any) -> FarmContext:
        await sync_registry(pool, reg or registry)
        fields.setdefault("executors", {"api": ApiExecutor()})
        fields.setdefault("poll_interval_s", 0.02)
        ctx = FarmContext(pool=pool, **fields)
        contexts.append(ctx)
        return ctx

    yield make
    for ctx in contexts:
        await ctx.aclose()
    # A synced registry changes the singleton farm_settings row, which M1a's schema test reads from the shared
    # session database without going through ``pool``: leave the database as clean as it was found.
    await truncate_all(pool)


@pytest_asyncio.fixture
async def clean_pool(pool: DbPool) -> AsyncIterator[DbPool]:
    """``pool`` that is emptied again afterwards (for tests that sync a registry without ``farm_factory``)."""
    yield pool
    await truncate_all(pool)


@pytest_asyncio.fixture
async def farm_ctx(farm_factory: Callable[..., Awaitable[FarmContext]], registry: Registry) -> FarmContext:
    """The fixture registry synced into the test database: reoon-01, reoon-02, zerobounce-01, clay, claude."""
    return await farm_factory(registry)
