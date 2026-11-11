"""Team credentials and queued administrative commands fail closed on membership changes."""

from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from farm.control.commands import process_command
from farm.control.run import create_token, verify_token
from farm.db.pool import DbPool, system_connection
from farm.gateway.middleware import tool_scope_allows
from farm.tenancy import MILVOW_TENANT_ID, use_tenant
from farm.tenancy.members import add_member, remove_member
from farm.tenancy.models import Role
from tests.conftest import seed_connection


async def test_member_token_carries_verified_user_and_removal_revokes_it(pool: DbPool) -> None:
    user = await add_member(pool, "alice@example.com", Role.MEMBER, str(uuid4()))
    raw = await create_token(pool, "codex-work", user_id=user, scopes=("mcp",))
    identity = await verify_token(pool, raw)
    assert identity is not None and identity.user_id == user and identity.user_email == "alice@example.com"
    assert await remove_member(pool, "ALICE@example.com")
    assert await verify_token(pool, raw) is None
    async with system_connection(pool) as conn:
        row = await (await conn.execute("select revoked_at from public.api_tokens")).fetchone()
    assert row is not None and row[0] is not None


async def test_removed_membership_rejects_token_even_if_revocation_was_not_called(pool: DbPool) -> None:
    user = await add_member(pool, "alice@example.com", Role.MEMBER, str(uuid4()))
    raw = await create_token(pool, "ide", user_id=user)
    async with system_connection(pool) as conn:
        await conn.execute("delete from public.memberships where user_id = %s", (user,))
    assert await verify_token(pool, raw) is None


async def test_token_binding_rejects_outsiders_and_other_tenant_members(pool: DbPool) -> None:
    with pytest.raises(ValueError, match="member"):
        await create_token(pool, "outsider", user_id=uuid4())
    user = await add_member(pool, "alice@example.com", Role.MEMBER, str(uuid4()))
    other = uuid4()
    async with system_connection(pool) as conn:
        await conn.execute("insert into public.tenants (id, slug, name) values (%s, 'other', 'Other')", (other,))
    with use_tenant(other), pytest.raises(ValueError, match="member"):
        await create_token(pool, "wrong-tenant", user_id=user)


async def test_empty_and_unknown_token_scopes_are_rejected(pool: DbPool) -> None:
    for scopes in ((), ("",), ("tool:*",), ("admin",)):
        with pytest.raises(ValueError, match="scopes"):
            await create_token(pool, "bad", scopes=scopes)


def test_scopes_allow_only_explicit_tool_or_mcp_invocation() -> None:
    assert tool_scope_allows(("tool:list_ais",), "list_ais")
    assert not tool_scope_allows(("tool:list_ais",), "ai_start")
    assert not tool_scope_allows((), "farm_guide")
    assert tool_scope_allows(("mcp",), "ai_recover")
    assert tool_scope_allows(("ai:recovery:approve",), "ai_recover")
    assert not tool_scope_allows(("ai:recovery:approve",), "ai_start")


@pytest.mark.parametrize("current_role", ["member", None])
async def test_queued_admin_command_is_rejected_after_demotion_or_removal(
    pool: DbPool, current_role: str | None,
) -> None:
    await seed_connection(pool, "pool", "account")
    user = await add_member(pool, "admin@example.com", Role.ADMIN, str(uuid4()))
    command_id = uuid4()
    async with system_connection(pool) as conn:
        await conn.execute(
            "insert into public.farm_commands "
            "(id, tenant_id, kind, payload, created_by, created_by_user_id, requires_actor_authorization) "
            "values (%s, %s, 'pause', %s, 'forged-owner', %s, true)",
            (command_id, MILVOW_TENANT_ID, Jsonb({"connection_id": "account"}), user),
        )
        if current_role is None:
            await conn.execute("delete from public.memberships where user_id = %s", (user,))
        else:
            await conn.execute("update public.memberships set role = %s where user_id = %s", (current_role, user))
    status, _ = await process_command(pool, command_id)
    assert status == "rejected"
    async with system_connection(pool) as conn:
        row = await (await conn.execute("select status from public.connections where id = 'account'")).fetchone()
    assert row == ("active",)


async def test_admin_command_audit_uses_database_identity_not_supplied_label(pool: DbPool) -> None:
    await seed_connection(pool, "pool", "account")
    user = await add_member(pool, "real-admin@example.com", Role.ADMIN, str(uuid4()))
    command_id = uuid4()
    async with system_connection(pool) as conn:
        await conn.execute(
            "insert into public.farm_commands "
            "(id, tenant_id, kind, payload, created_by, created_by_user_id, requires_actor_authorization) "
            "values (%s, %s, 'pause', %s, 'forged-owner', %s, true)",
            (command_id, MILVOW_TENANT_ID, Jsonb({"connection_id": "account"}), user),
        )
    status, _ = await process_command(pool, command_id)
    assert status == "done"
    async with system_connection(pool) as conn:
        rows = await (await conn.execute("select actor from public.audit_events")).fetchall()
    assert rows == [("real-admin@example.com",)]
