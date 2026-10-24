"""F1A: tenants are real rows and every tenant-owned table carries ``tenant_id`` linked to them."""

from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from alembic import command
from psycopg import errors

from farm.control.cli import alembic_config
from farm.db.pool import DbPool
from farm.tenancy import MILVOW_TENANT_ID, Role, TenantContext
from tests.conftest import seed_connection

PLATFORM_TABLES = {"alembic_version", "tenants", "users"}
# Created at runtime by farm/control/run.py, not by a migration: it appears in the shared test database once
# test_run has run. F1B moves it into a migration with a tenant_id.
RUNTIME_TABLES = {"client_tokens"}
MILVOW = str(MILVOW_TENANT_ID)


def _rows(url: str, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(url, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


def _tenant_tables(url: str) -> list[str]:
    rows = _rows(url, "select tablename from pg_tables where schemaname = 'public'")
    return sorted(r[0] for r in rows if r[0] not in PLATFORM_TABLES | RUNTIME_TABLES)


def test_every_tenant_table_has_tenant_id_fk_and_leading_index(db_url: str) -> None:
    tables = _tenant_tables(db_url)
    assert {"connections", "runs", "pool_state", "integration_requests", "memberships", "farm_settings"} <= set(tables)
    not_null = {
        r[0]
        for r in _rows(
            db_url,
            "select table_name from information_schema.columns where table_schema = 'public' "
            "and column_name = 'tenant_id' and data_type = 'uuid' and is_nullable = 'NO'",
        )
    }
    fks = {
        r[0]
        for r in _rows(
            db_url,
            "select conrelid::regclass::text from pg_constraint c where contype = 'f' "
            "and confrelid = 'public.tenants'::regclass and conkey = array["
            "(select attnum from pg_attribute where attrelid = c.conrelid and attname = 'tenant_id')]",
        )
    }
    indexed = {
        r[0]
        for r in _rows(
            db_url,
            "select i.indrelid::regclass::text from pg_index i join pg_attribute a "
            "on a.attrelid = i.indrelid and a.attnum = i.indkey[0] where a.attname = 'tenant_id'",
        )
    }
    assert [t for t in tables if t not in not_null] == []
    assert [t for t in tables if t not in fks] == []
    assert [t for t in tables if t not in indexed] == []
    assert _rows(db_url, "select table_name from information_schema.columns where column_name = 'workspace_id'") == []


def test_tenant_one_is_milvow(db_url: str) -> None:
    assert _rows(db_url, "select id::text, slug, name, cell_id, plan, status from public.tenants") == [
        (MILVOW, "milvow", "Milvow", "cell-1", "beta", "active")
    ]


async def test_insert_without_tenant_lands_in_milvow(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c")
    async with pool.connection() as conn:
        row = await (await conn.execute("select tenant_id::text from connections where id = 'c'")).fetchone()
    assert row == (MILVOW,)


async def test_unknown_tenant_is_rejected(pool: DbPool) -> None:
    async with pool.connection() as conn:
        with pytest.raises(errors.ForeignKeyViolation):
            await conn.execute(
                "insert into providers (id, name, kind, executor, tenant_id) "
                "values ('x', 'x', 'tool', 'api', gen_random_uuid())"
            )


async def test_request_hash_is_unique_per_tenant(pool: DbPool) -> None:
    async with pool.connection() as conn:
        other = await (
            await conn.execute("insert into tenants (slug, name) values ('other', 'Other') returning id")
        ).fetchone()
        assert other is not None
        insert = (
            "insert into capability_requests (request_hash, capability, status, tenant_id) "
            "values ('h', 'cap', 'pending', %s)"
        )
        await conn.execute(insert, (MILVOW,))
        await conn.execute(insert, (other[0],))  # same hash, other tenant: fine
        with pytest.raises(errors.UniqueViolation):
            await conn.execute(insert, (MILVOW,))


async def test_membership_rules(pool: DbPool) -> None:
    async with pool.connection() as conn:
        user = await (
            await conn.execute("insert into users (email) values ('Jane@Example.com') returning id")
        ).fetchone()
        assert user is not None
        with pytest.raises(errors.UniqueViolation):
            await conn.execute("insert into users (email) values ('jane@example.com')")
        with pytest.raises(errors.CheckViolation):
            await conn.execute(
                "insert into memberships (tenant_id, user_id, role) values (%s, %s, 'god')", (MILVOW, user[0])
            )
        await conn.execute(
            "insert into memberships (tenant_id, user_id, role) values (%s, %s, 'owner')", (MILVOW, user[0])
        )
        await conn.execute("delete from users where id = %s", (user[0],))
        assert await (await conn.execute("select count(*) from memberships")).fetchone() == (0,)


def test_tenant_context_is_frozen() -> None:
    ctx = TenantContext(tenant_id=MILVOW_TENANT_ID, user_id=None, roles=frozenset({Role.OWNER}), cell_id="cell-1")
    with pytest.raises(ValueError, match="frozen"):
        ctx.cell_id = "cell-2"


def test_downgrade_upgrade_round_trip_with_data(scratch_db: Callable[[], str]) -> None:
    """Rows written before 0014 keep their data and land in Milvow; downgrade restores workspace_id."""
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "0013")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("insert into providers (id, name, kind, executor) values ('p', 'P', 'tool', 'api')")
        conn.execute("insert into connections (id, provider_id) values ('c', 'p')")
        conn.execute("insert into pool_state (provider_id, rr_cursor) values ('p', 3)")
        conn.execute("insert into capability_requests (request_hash, capability, status) values ('h', 'cap', 'pending')")
        conn.execute("insert into integration_requests (name, kind, purpose) values ('n', 'mcp', 'why')")
    tables_before = _tenant_tables(url)

    for _ in range(2):
        command.upgrade(cfg, "head")
        assert _rows(url, "select tenant_id::text, id from public.connections") == [(MILVOW, "c")]
        assert _rows(url, "select tenant_id::text, rr_cursor from public.pool_state") == [(MILVOW, 3)]
        assert _rows(url, "select tenant_id::text from public.integration_requests") == [(MILVOW,)]
        assert _rows(url, "select tenant_id::text from public.capability_requests") == [(MILVOW,)]
        assert _tenant_tables(url) == sorted([*tables_before, "memberships"])

        command.downgrade(cfg, "0013")
        assert _rows(url, "select to_regclass('public.tenants')::text") == [(None,)]
        assert _rows(url, "select workspace_id::text from public.connections") == [(MILVOW,)]
        assert _rows(url, "select workspace_id::text from public.capability_requests") == [(MILVOW,)]
        assert _rows(
            url,
            "select table_name from information_schema.columns where table_schema = 'public' "
            "and (column_name = 'tenant_id' or (column_name = 'workspace_id' "
            "and table_name in ('pool_state', 'integration_requests')))",
        ) == []
        assert _tenant_tables(url) == tables_before
