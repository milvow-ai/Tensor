"""F1B: the tenant context, the checkouts, the token migration and the guard against unscoped checkouts."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from alembic import command

from farm.control.cli import alembic_config
from farm.control.run import create_token, hash_token, list_tokens, revoke_token, verify_token
from farm.db.pool import DbPool, system_connection, tenant_connection
from farm.tenancy import MILVOW_TENANT_ID, TenantContextMissing, current_tenant, use_tenant, without_tenant
from farm.tenancy.active import for_each_tenant
from tests.conftest import TwoTenants

FARM = Path(__file__).resolve().parent.parent / "farm"

# Raw ``pool.connection()`` (the owner checkout) is allowed only here. ledger.py and budgets.py belong to Q0G's
# round (they could not be edited by F1B): they must move to tenant_connection / system_connection once it merges.
OWNER_CHECKOUT_ALLOWED = {"db/pool.py", "resources/ledger.py", "manager/budgets.py"}


def test_no_request_scoped_code_uses_the_owner_checkout() -> None:
    offenders = []
    for path in sorted(FARM.rglob("*.py")):
        rel = path.relative_to(FARM).as_posix()
        if rel in OWNER_CHECKOUT_ALLOWED or rel.startswith("db/migrations/"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\b\w*pool\.connection\(", line) and "tenant_connection" not in line:
                offenders.append(f"{rel}:{number}")
    assert offenders == []


def test_tenant_context_nests_and_restores() -> None:
    a, b = UUID(int=1), UUID(int=2)
    with use_tenant(a):
        with use_tenant(b):
            assert current_tenant.get() == b
            with without_tenant():
                assert current_tenant.get() is None
            assert current_tenant.get() == b
        assert current_tenant.get() == a


async def test_background_tasks_inherit_the_tenant_of_the_request_that_started_them() -> None:
    async def read() -> UUID | None:
        await asyncio.sleep(0)
        return current_tenant.get()

    other = UUID(int=7)
    with use_tenant(other):
        task = asyncio.create_task(read())
    assert await task == other  # the copy was taken at create_task, not at run time


async def test_the_checkout_refuses_without_a_tenant_and_sets_it_otherwise(pool: DbPool) -> None:
    with without_tenant(), pytest.raises(TenantContextMissing):
        async with tenant_connection(pool):
            pass
    other = UUID(int=9)
    with use_tenant(other):
        async with tenant_connection(pool) as conn:
            row = await (await conn.execute("select public.farm_tenant_id(), current_user")).fetchone()
    assert row == (other, "farm_app")


async def test_for_each_tenant_runs_each_in_its_own_context_and_survives_a_failure(
    pool: DbPool, two_tenants: TwoTenants
) -> None:
    seen: list[UUID | None] = []

    async def work() -> UUID | None:
        seen.append(current_tenant.get())
        if current_tenant.get() == two_tenants.a:
            raise RuntimeError("tenant A's pass failed")
        return current_tenant.get()

    with without_tenant():
        results = await for_each_tenant(pool, work, task="probe")
    assert set(seen) == {two_tenants.a, two_tenants.b}
    assert results == [two_tenants.b]  # A's failure did not stop B


async def test_token_resolves_to_its_tenant_and_dies_with_revocation_or_suspension(
    pool: DbPool, two_tenants: TwoTenants
) -> None:
    with use_tenant(two_tenants.b):
        raw = await create_token(pool, "b-client")
    with without_tenant():
        identity = await verify_token(pool, raw)
    assert identity is not None and identity.tenant_id == two_tenants.b and identity.name == "b-client"
    with use_tenant(two_tenants.a):
        assert [t["client_name"] for t in await list_tokens(pool)] == ["iso-a-text"]  # not B's token
        assert await revoke_token(pool, "b-client") is False
    async with system_connection(pool) as conn:
        await conn.execute("update public.tenants set status = 'suspended' where id = %s", (two_tenants.b,))
    assert await verify_token(pool, raw) is None  # a suspended tenant's tokens stop working
    async with system_connection(pool) as conn:
        await conn.execute("update public.tenants set status = 'active' where id = %s", (two_tenants.b,))
    with use_tenant(two_tenants.b):
        assert await revoke_token(pool, "b-client") is True
    assert await verify_token(pool, raw) is None


async def test_only_the_hash_is_stored(pool: DbPool) -> None:
    raw = await create_token(pool, "hash-check")
    async with tenant_connection(pool) as conn:
        row = await (await conn.execute("select token_hash, token_prefix from public.api_tokens")).fetchone()
    assert row == (hashlib.sha256(raw.encode()).hexdigest(), raw[:8])
    assert hash_token(raw) == row[0]


def test_client_tokens_migrate_to_tenant_one_hashed_and_downgrade_gives_them_back(
    scratch_db: Callable[[], str],
) -> None:
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "0017")
    digest = hashlib.sha256(b"farm_live-token").hexdigest()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "create table public.client_tokens (token_hash text primary key, client_name text not null, "
            "created_at timestamptz not null default now(), last_used timestamptz)"
        )
        conn.execute(
            "insert into public.client_tokens (token_hash, client_name) values (%s, 'cursor'), ('plain-secret ', 'old')",
            (digest,),
        )
    command.upgrade(cfg, "head")
    with psycopg.connect(url, autocommit=True) as conn:
        rows = conn.execute(
            "select name, token_hash, tenant_id, user_id, scopes, revoked_at, token_prefix "
            "from public.api_tokens order by name"
        ).fetchall()
        assert conn.execute("select to_regclass('public.client_tokens')").fetchone() == (None,)
    assert rows == [
        ("cursor", digest, MILVOW_TENANT_ID, None, ["*"], None, "farm_"),
        ("old", hashlib.sha256(b"plain-secret").hexdigest(), MILVOW_TENANT_ID, None, ["*"], None, "farm_"),
    ]
    command.downgrade(cfg, "0017")
    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute("select token_hash, client_name from public.client_tokens order by 2").fetchall() == [
            (digest, "cursor"),
            (hashlib.sha256(b"plain-secret").hexdigest(), "old"),
        ]
        assert conn.execute("select to_regclass('public.api_tokens')").fetchone() == (None,)
    command.upgrade(cfg, "head")  # and forward again
