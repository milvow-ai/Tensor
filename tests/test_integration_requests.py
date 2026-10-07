"""Acceptance tests for integration requests (GUIDE1b)."""

from __future__ import annotations

import pytest
from fastmcp import Client

from farm.context import FarmContext
from farm.control.integration_requests import (
    execute_resolve_integration_request,
    get_integration_request,
    list_integration_requests,
    request_integration,
    resolve_integration_request,
)
from farm.db.pool import DbPool
from farm.gateway.server import build_server


async def test_request_integration_creates_row_and_alert(pool: DbPool) -> None:
    req, dedup = await request_integration(
        pool,
        name="github",
        kind="mcp",
        purpose="Need GitHub MCP server to inspect PR comments and issues",
        task_context="Automating PR reviews",
        urgency="now",
        links=["https://github.com/modelcontextprotocol/servers"],
        requested_by="test-client",
        notify_telegram=False,
    )

    assert dedup is False
    assert req.name == "github"
    assert req.kind == "mcp"
    assert req.status == "open"
    assert req.urgency == "now"
    assert req.requested_by == "test-client"
    assert "PR comments" in req.purpose
    assert req.links == ["https://github.com/modelcontextprotocol/servers"]

    # Verify stored in DB
    stored = await get_integration_request(pool, req.id)
    assert stored is not None
    assert stored.id == req.id
    assert stored.name == "github"

    # Verify alert was recorded in public.alerts
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select kind, severity, message from public.alerts where kind = 'integration_request' limit 1"
        )
        alert_row = await cur.fetchone()
        assert alert_row is not None
        assert alert_row[0] == "integration_request"
        assert alert_row[1] == "warn"  # urgency="now" gives severity="warn"
        assert "github" in alert_row[2]


async def test_request_integration_dedupes_identical_open_requests(pool: DbPool) -> None:
    req1, dedup1 = await request_integration(
        pool,
        name="salesforce",
        kind="mcp",
        purpose="Need CRM integration for contacts",
        urgency="soon",
        links=["https://salesforce.com/docs"],
        notify_telegram=False,
    )
    assert dedup1 is False

    # Second request with same name + kind while open
    req2, dedup2 = await request_integration(
        pool,
        name="salesforce",
        kind="mcp",
        purpose="Need opportunity pipeline tracking",
        urgency="now",
        links=["https://salesforce.com/api"],
        notify_telegram=False,
    )
    assert dedup2 is True
    assert req2.id == req1.id
    assert "CRM integration" in req2.purpose
    assert "opportunity pipeline tracking" in req2.purpose
    assert req2.urgency == "now"  # bumped to now
    assert set(req2.links) == {"https://salesforce.com/docs", "https://salesforce.com/api"}

    # Only one row exists in DB
    all_reqs = await list_integration_requests(pool)
    assert len([r for r in all_reqs if r.name == "salesforce"]) == 1


@pytest.mark.parametrize(
    "secret_val",
    [
        "sk-" + "ant-api03-abcdef1234567890abcdef123456",
        "sk-" + "proj-1234567890abcdef1234567890abcdef12345",
        "ghp_" + "1234567890abcdef1234567890abcdef123456",
        "AK" + "IAIOSFODNN7EXAMPLE",
        "https://example.com/api?key=abcdef123456",
    ],
)
async def test_request_integration_rejects_key_shaped_strings(pool: DbPool, secret_val: str) -> None:
    # In purpose
    with pytest.raises(ValueError, match="secret"):
        await request_integration(
            pool,
            name="my-api",
            kind="api",
            purpose=f"Here is the key: {secret_val}",
        )

    # In name
    with pytest.raises(ValueError, match="secret"):
        await request_integration(
            pool,
            name=secret_val,
            kind="other",
            purpose="Testing secret name",
        )

    # In links
    with pytest.raises(ValueError, match="secret"):
        await request_integration(
            pool,
            name="safe-api",
            kind="api",
            purpose="Testing secret link",
            links=[f"https://api.example.com?token={secret_val}"],
        )

    # Database remains empty
    all_reqs = await list_integration_requests(pool)
    assert len(all_reqs) == 0


async def test_list_integration_requests_filters_by_status(pool: DbPool) -> None:
    r1, _ = await request_integration(pool, name="tool-a", kind="cli", purpose="A", notify_telegram=False)
    r2, _ = await request_integration(pool, name="tool-b", kind="api", purpose="B", notify_telegram=False)
    r3, _ = await request_integration(pool, name="tool-c", kind="account", purpose="C", notify_telegram=False)

    await resolve_integration_request(pool, r2.id, status="in_progress")
    await resolve_integration_request(pool, r3.id, status="done", owner_note="Ready")

    all_reqs = await list_integration_requests(pool)
    assert len(all_reqs) == 3

    open_reqs = await list_integration_requests(pool, status="open")
    assert len(open_reqs) == 1
    assert open_reqs[0].id == r1.id

    in_progress_reqs = await list_integration_requests(pool, status="in_progress")
    assert len(in_progress_reqs) == 1
    assert in_progress_reqs[0].id == r2.id

    done_reqs = await list_integration_requests(pool, status="done")
    assert len(done_reqs) == 1
    assert done_reqs[0].id == r3.id
    assert done_reqs[0].owner_note == "Ready"
    assert done_reqs[0].resolved_at is not None


async def test_resolve_via_command_updates_it(pool: DbPool) -> None:
    req, _ = await request_integration(pool, name="notion", kind="mcp", purpose="Need Notion MCP", notify_telegram=False)

    status, res = await execute_resolve_integration_request(
        pool,
        payload={"request_id": str(req.id), "status": "done", "owner_note": "Imported via farm mcp import"},
        actor="console",
    )
    assert status == "done"
    assert res["status"] == "done"

    updated = await get_integration_request(pool, req.id)
    assert updated is not None
    assert updated.status == "done"
    assert updated.owner_note == "Imported via farm mcp import"
    assert updated.resolved_at is not None


async def test_gateway_mcp_request_and_list_tools(
    pool: DbPool,
    farm_ctx: FarmContext,
) -> None:
    server = await build_server(farm_ctx)

    async with Client(server) as client:
        # Request tool
        res = await client.call_tool(
            "request_integration",
            {
                "name": "linear",
                "kind": "mcp",
                "purpose": "Need Linear integration to manage tickets",
                "urgency": "soon",
            },
        )
        content = res.structured_content
        assert content is not None
        assert content["status"] == "open"
        assert content["deduplicated"] is False
        assert "owner will be notified" in content["message"]

        # List tool
        list_res = await client.call_tool("list_integration_requests", {"status": "open"})
        list_content = list_res.structured_content
        assert list_content is not None
        reqs = list_content["requests"]
        assert len(reqs) >= 1
        assert any(r["name"] == "linear" for r in reqs)


async def test_cli_requests_commands(pool: DbPool, db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from farm.control.cli import app

    monkeypatch.setenv("FARM_DB_URL", db_url)
    runner = CliRunner()

    req, _ = await request_integration(pool, name="hubspot", kind="api", purpose="Need HubSpot API", notify_telegram=False)

    # farm requests list
    res = runner.invoke(app, ["requests", "list"])
    assert res.exit_code == 0
    assert "hubspot" in res.stdout

    # farm requests show
    res_show = runner.invoke(app, ["requests", "show", str(req.id)])
    assert res_show.exit_code == 0
    assert "hubspot" in res_show.stdout
    assert "Need HubSpot API" in res_show.stdout

    # farm requests resolve
    res_res = runner.invoke(
        app,
        ["requests", "resolve", str(req.id), "--status", "done", "--note", "Added credentials"],
    )
    assert res_res.exit_code == 0
    assert "resolved: done" in res_res.stdout

    # Verify updated in DB
    updated = await get_integration_request(pool, req.id)
    assert updated is not None
    assert updated.status == "done"
    assert updated.owner_note == "Added credentials"

