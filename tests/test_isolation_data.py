"""F1B isolation, data layer: tenant A can never read or change tenant B's rows, in any table or view.

The tables and views come from the database at test time, so a new tenant table or view without coverage here
fails ``test_every_tenant_table_is_covered`` / the view tests instead of going unnoticed.
"""

from __future__ import annotations

import uuid

import pytest
from psycopg import errors

from farm.db.pool import DbPool, open_pool, system_connection, tenant_connection
from farm.tenancy import TenantContextMissing, use_tenant, without_tenant
from tests.conftest import SEEDED_ELSEWHERE, SEEDS, TwoTenants, tenant_params

PLATFORM_TABLES = {"alembic_version", "tenants", "users", "farm_heartbeats"}


async def _tenant_tables(pool: DbPool) -> set[str]:
    async with system_connection(pool) as conn:
        cur = await conn.execute(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind in ('r', 'p') and exists ("
            "  select 1 from pg_attribute a where a.attrelid = c.oid and a.attname = 'tenant_id' "
            "  and not a.attisdropped)"
        )
        return {r[0] for r in await cur.fetchall()} - PLATFORM_TABLES


async def test_every_tenant_table_is_covered(pool: DbPool) -> None:
    tables = await _tenant_tables(pool)
    assert tables - {"farm_settings"} == set(SEEDS) | (SEEDED_ELSEWHERE - {"farm_settings"})


async def test_every_tenant_table_is_default_filled_and_policed(pool: DbPool) -> None:
    async with system_connection(pool) as conn:
        for table in sorted(await _tenant_tables(pool)):
            cur = await conn.execute(
                "select c.relrowsecurity, pg_get_expr(d.adbin, d.adrelid), "
                "(select count(*) from pg_policies p where p.schemaname = 'public' and p.tablename = c.relname "
                " and p.policyname = 'tenant_isolation' and 'farm_app' = any (p.roles)) "
                "from pg_class c join pg_attribute a on a.attrelid = c.oid and a.attname = 'tenant_id' "
                "left join pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum "
                "where c.oid = %s::regclass",
                (f"public.{table}",),
            )
            assert await cur.fetchone() == (True, "farm_tenant_id()", 1), table


@pytest.mark.parametrize("table", sorted(SEEDS))
async def test_reads_and_writes_never_cross_tenants(pool: DbPool, two_tenants: TwoTenants, table: str) -> None:
    a, b = two_tenants.a, two_tenants.b
    for me, other in ((a, b), (b, a)):
        with use_tenant(me):
            async with tenant_connection(pool) as conn:
                # (a) the other tenant's rows are invisible, by filter and by id
                row = await (await conn.execute(f"select count(*) from public.{table}")).fetchone()  # type: ignore[call-overload]
                mine = await (
                    await conn.execute(f"select count(*) from public.{table} where tenant_id = %s", (me,))  # type: ignore[call-overload]
                ).fetchone()
                theirs = await (
                    await conn.execute(f"select count(*) from public.{table} where tenant_id = %s", (other,))  # type: ignore[call-overload]
                ).fetchone()
                assert row == mine and mine is not None and mine[0] >= 1, table
                assert theirs == (0,), table
                # (b) writing with the other tenant's ids changes nothing and cannot move rows across
                updated = await conn.execute(
                    f"update public.{table} set tenant_id = tenant_id where tenant_id = %s", (other,)  # type: ignore[call-overload]
                )
                deleted = await conn.execute(f"delete from public.{table} where tenant_id = %s", (other,))  # type: ignore[call-overload]
                assert updated.rowcount == 0 and deleted.rowcount == 0, table
                with pytest.raises(errors.InsufficientPrivilege):
                    await conn.execute(
                        f"update public.{table} set tenant_id = %s", (other,)  # type: ignore[call-overload]
                    )
                columns, values = SEEDS[table]
                with pytest.raises(errors.InsufficientPrivilege):  # inserting a row for the other tenant
                    await conn.execute(
                        f"insert into public.{table} (tenant_id, {columns}) values (%(tenant)s, {values})",  # type: ignore[call-overload]
                        {**two_tenants.params[me], "tenant": other},
                    )
    async with system_connection(pool) as conn:  # nothing of the above touched either tenant's rows
        for tenant in (a, b):
            cur = await conn.execute(f"select count(*) from public.{table} where tenant_id = %s", (tenant,))  # type: ignore[call-overload]
            assert await cur.fetchone() == (1,), table


@pytest.mark.parametrize("table", sorted(SEEDS))
async def test_an_insert_without_a_tenant_fails(
    pool: DbPool, db_url: str, monkeypatch: pytest.MonkeyPatch, table: str
) -> None:
    columns, values = SEEDS[table]
    params = tenant_params("none")
    statement = f"insert into public.{table} ({columns}) values ({values})"
    # A pool as production opens it: the test harness's PGOPTIONS default (tenant #1) is not in its connections.
    monkeypatch.delenv("PGOPTIONS")
    bare = await open_pool(db_url)
    try:
        with without_tenant():
            # the owner checkout carries no tenant: the column default is NULL and NOT NULL refuses the row
            async with bare.connection() as conn:
                with pytest.raises(errors.NotNullViolation):
                    await conn.execute(statement, params)  # type: ignore[call-overload]
            # the tenant checkout refuses before touching the database
            with pytest.raises(TenantContextMissing):
                async with tenant_connection(bare):
                    pass
        # the platform role has no default either
        async with system_connection(bare) as conn:
            with pytest.raises(errors.NotNullViolation):
                await conn.execute(statement, params)  # type: ignore[call-overload]
    finally:
        await bare.close()


async def test_memberships_follow_the_same_rules(pool: DbPool, two_tenants: TwoTenants) -> None:
    with use_tenant(two_tenants.a):
        async with tenant_connection(pool) as conn:
            rows = await (await conn.execute("select tenant_id from public.memberships")).fetchall()
            assert rows == [(two_tenants.a,)]
            changed = await conn.execute(
                "update public.memberships set role = 'member' where tenant_id = %s", (two_tenants.b,)
            )
            assert changed.rowcount == 0


async def test_the_runtime_role_cannot_touch_platform_tables(pool: DbPool, two_tenants: TwoTenants) -> None:
    with use_tenant(two_tenants.a):
        for table in ("tenants", "users", "farm_heartbeats"):
            async with tenant_connection(pool) as conn:
                with pytest.raises(errors.InsufficientPrivilege):
                    await conn.execute(f"select * from public.{table}")  # type: ignore[call-overload]


async def test_a_checkout_does_not_leak_its_role_or_tenant_to_the_next_user(pool: DbPool) -> None:
    probe = uuid.uuid4()  # an unknown tenant is fine for a role probe
    with use_tenant(probe):
        async with tenant_connection(pool) as conn:
            who = await (await conn.execute("select current_user, current_setting('app.tenant_id')")).fetchone()
            assert who == ("farm_app", str(probe))
    with without_tenant():
        async with pool.connection() as conn:
            after = await (await conn.execute("select current_user, current_setting('app.tenant_id', true)")).fetchone()
        # back to the owner role, and the probe tenant is gone (the harness's own default may remain)
        assert after is not None and after[0] != "farm_app" and after[1] != str(probe)


async def test_the_roles_are_what_the_design_says(pool: DbPool) -> None:
    async with system_connection(pool) as conn:
        rows = await (
            await conn.execute(
                "select rolname, rolbypassrls, rolcanlogin from pg_roles where rolname in ('farm_app', 'farm_system') "
                "order by rolname"
            )
        ).fetchall()
    assert rows == [("farm_app", False, False), ("farm_system", True, False)]


async def test_every_view_hides_the_other_tenant_and_applies_the_readers_rls(
    pool: DbPool, two_tenants: TwoTenants
) -> None:
    async with system_connection(pool) as conn:
        cur = await conn.execute(
            "select c.relname, coalesce(c.reloptions::text[] && array['security_invoker=true', 'security_invoker=on'], false) "
            "from pg_class c join pg_namespace n on n.oid = c.relnamespace "
            "where n.nspname = 'public' and c.relkind = 'v' order by 1"
        )
        views = await cur.fetchall()
    assert len(views) >= 10
    assert [v for v, invoker in views if not invoker] == []
    nonempty = 0
    for me, other_tag in ((two_tenants.a, "iso-b-"), (two_tenants.b, "iso-a-")):
        with use_tenant(me):
            async with tenant_connection(pool) as conn:
                for view, _ in views:
                    cur = await conn.execute(f"select * from public.{view}")  # type: ignore[call-overload]
                    rows = await cur.fetchall()
                    nonempty += bool(rows)
                    assert other_tag not in repr(rows), view
    assert nonempty >= 10  # the check above looked at real rows, not only empty views
