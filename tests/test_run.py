"""Tests for farm run, client tokens, auth middleware, and caller tracking.

Covers:
- Client token lifecycle: creation, hashing, verification, revocation, last_used.
- CLI commands: farm token create, list, revoke.
- Health endpoint: /health returns 200 without auth.
- MCP endpoint auth: missing/invalid/revoked token returns HTTP 401 with WWW-Authenticate.
- Caller tracking: authenticated client name is mapped into runs.caller for tool executions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from typer.testing import CliRunner

from farm.context import FarmContext
from farm.control.cli import app
from farm.control.run import (
    create_farm_app,
    create_token,
    ensure_tokens_table,
    hash_token,
    list_tokens,
    revoke_token,
    verify_token,
)
from farm.db.pool import DbPool
from farm.registry import Registry
from tests.farm_helpers import EMAIL, ScriptedExecutor, ok_result

type Make = Callable[..., Awaitable[FarmContext]]


async def test_token_lifecycle_and_verification(pool: DbPool) -> None:
    """Token creation, verification, last_used update, and revocation."""
    await ensure_tokens_table(pool)

    # 1. Create token
    raw_token = await create_token(pool, "test-agent")
    assert raw_token.startswith("farm_")
    assert len(raw_token) > 20

    # Token hash in DB, raw token not in DB
    expected_hash = hash_token(raw_token)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select token_hash, client_name, last_used from public.client_tokens where client_name = %s",
            ("test-agent",),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == expected_hash
        assert row[1] == "test-agent"
        assert row[2] is None  # last_used initially None

    # 2. Verify token
    client = await verify_token(pool, raw_token)
    assert client == "test-agent"

    # last_used is updated
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select last_used from public.client_tokens where client_name = %s",
            ("test-agent",),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] is not None

    # 3. Invalid token returns None
    assert await verify_token(pool, "farm_invalid_token_value_here") is None
    assert await verify_token(pool, "") is None
    assert await verify_token(pool, "wrong_prefix") is None

    # 4. List tokens
    tokens = await list_tokens(pool)
    assert len(tokens) >= 1
    t = next(x for x in tokens if x["client_name"] == "test-agent")
    assert "token" not in t  # raw secret is never returned

    # 5. Revoke token
    revoked = await revoke_token(pool, "test-agent")
    assert revoked is True
    assert await verify_token(pool, raw_token) is None

    tokens_after = await list_tokens(pool)
    assert not any(x["client_name"] == "test-agent" for x in tokens_after)


def test_cli_token_commands(db_url: str) -> None:
    """CLI token create, list, and revoke commands."""
    runner = CliRunner()

    # 1. Create token via CLI
    res_create = runner.invoke(app, ["token", "create", "cli-agent"], env={"DATABASE_URL": db_url})
    assert res_create.exit_code == 0
    assert "Created token for 'cli-agent':" in res_create.stdout
    assert "farm_" in res_create.stdout
    lines = res_create.stdout.splitlines()
    raw_token = next(line.strip() for line in lines if line.strip().startswith("farm_"))

    # 2. List tokens
    res_list = runner.invoke(app, ["token", "list"], env={"DATABASE_URL": db_url})
    assert res_list.exit_code == 0
    assert "cli-agent" in res_list.stdout
    # Secret itself must NOT appear in token list
    assert raw_token not in res_list.stdout

    # 3. Revoke token
    res_revoke = runner.invoke(app, ["token", "revoke", "cli-agent"], env={"DATABASE_URL": db_url})
    assert res_revoke.exit_code == 0
    assert "revoked token for 'cli-agent'" in res_revoke.stdout.lower()

    # 4. List tokens shows revoked (absent from active list)
    res_list2 = runner.invoke(app, ["token", "list"], env={"DATABASE_URL": db_url})
    assert res_list2.exit_code == 0
    assert "cli-agent" not in res_list2.stdout


async def test_health_endpoint_no_auth(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    """The /health endpoint returns 200 OK without requiring authentication."""
    ctx = await farm_factory(registry)
    app = await create_farm_app(ctx)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
    ) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"


async def test_mcp_endpoint_auth_rejection(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    """Requests to /mcp without a valid Bearer token return HTTP 401."""
    await ensure_tokens_table(pool)
    raw_token = await create_token(pool, "valid-client")

    ctx = await farm_factory(registry)
    app = await create_farm_app(ctx)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
    ) as client:
        # No auth header -> 401
        r1 = await client.post("/mcp", json={})
        assert r1.status_code == 401
        assert r1.headers.get("www-authenticate") == "Bearer"

        # Invalid token -> 401
        r2 = await client.post("/mcp", headers={"Authorization": "Bearer bad-token"}, json={})
        assert r2.status_code == 401

        # Basic auth instead of Bearer -> 401
        r3 = await client.post("/mcp", headers={"Authorization": f"Basic {raw_token}"}, json={})
        assert r3.status_code == 401

        # Revoke token -> now 401
        await revoke_token(pool, "valid-client")
        r4 = await client.post("/mcp", headers={"Authorization": f"Bearer {raw_token}"}, json={})
        assert r4.status_code == 401


async def test_mcp_auth_success_sets_runs_caller(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    """Authenticated MCP requests map the bearer token to the client name in runs.caller."""
    await ensure_tokens_table(pool)
    token_claude = await create_token(pool, "claude-code")

    executor = ScriptedExecutor(lambda req: ok_result())
    ctx = await farm_factory(registry, executors={"api": executor})
    app = await create_farm_app(ctx)

    def factory(
        headers: dict[str, str] | None = None,
        timeout: Any = None,
        auth: Any = None,
        **_: Any,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            headers=headers,
            timeout=timeout,
            auth=auth,
        )

    # Use FastMCP client with valid bearer token for claude-code
    transport = StreamableHttpTransport(
        "http://127.0.0.1/mcp",
        headers={"Authorization": f"Bearer {token_claude}"},
        httpx_client_factory=factory,
    )

    base_starlette_app = getattr(app, "app", app)
    async with base_starlette_app.router.lifespan_context(base_starlette_app):
        async with Client(transport) as mcp_client:
            res = await mcp_client.call_tool("verify_email", {"email": EMAIL})
            assert res.structured_content is not None
            assert res.structured_content.get("ok") is True

    # Verify that the run record has caller = 'claude-code'
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select caller, capability from public.runs order by started_at desc limit 1"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "claude-code"
        assert row[1] == "verify_email"
