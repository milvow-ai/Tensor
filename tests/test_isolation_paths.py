"""F1B isolation, entry points: MCP tools, command kinds and the cross-tenant workers cannot cross tenants.

Tool names come from the running server, command kinds from ``PAYLOAD_VALIDATORS``: a new tool or command kind
without a scenario here fails the coverage tests.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from farm.context import FarmContext
from farm.control.commands import PAYLOAD_VALIDATORS, poll_and_execute_queued, process_command
from farm.db.pool import DbPool, system_connection, tenant_connection
from farm.gateway.ai_tools import AI_TOOLS
from farm.gateway.server import INFRA_TOOLS, build_server
from farm.registry import Registry
from farm.tenancy import use_tenant
from tests.conftest import SEEDS, TwoTenants
from tests.farm_helpers import EMAIL, ScriptedExecutor, ok_result

type Make = Callable[..., Awaitable[FarmContext]]


async def snapshot(pool: DbPool, tenant: UUID) -> dict[str, str]:
    """A digest of every row the tenant owns, per table: any change by anybody else shows up."""
    out: dict[str, str] = {}
    async with system_connection(pool) as conn:
        for table in [*SEEDS, "memberships"]:
            cur = await conn.execute(
                f"select count(*), md5(coalesce(string_agg(t::text, ',' order by t::text), '')) "  # type: ignore[call-overload]
                f"from public.{table} t where tenant_id = %s",
                (tenant,),
            )
            out[table] = repr(await cur.fetchone())
    return out


# --- command kinds -----------------------------------------------------------------------------------------


def command_payloads(b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every command kind, aimed at tenant B's ids (``b`` = B's seed parameters)."""
    conn, prov, cap = b["conn"], b["pid"], b["cap"]
    return {
        "pause": {"connection_id": conn},
        "resume": {"connection_id": conn},
        "set_priority": {"connection_id": conn, "priority": 1},
        "set_strategy": {"strategy": "round_robin", "provider_id": prov},
        "set_budget": {"scope": "connection", "ref": conn, "monthly_usd": 1},
        "add_connection": {"provider_id": prov, "id": conn, "auth_ref": "env:ISO_B_KEY"},
        "update_connection": {"connection_id": conn, "label": "hijacked"},
        "remove_connection": {"connection_id": conn},
        "set_route": {"capability": cap, "provider_id": prov, "position": 5, "enabled": False},
        "test_connection": {"connection_id": conn},
        "ack_alert": {"alert_id": str(b["alert"])},
        "cancel_ai_job": {"job_id": str(b["jid"])},
        "set_max_parallel": {"connection_id": conn, "max_parallel": 9},
        "set_mcp_tool_access": {"provider_id": prov, "tool": "tool", "enabled": False},
        "sync_mcp_tools": {"provider_id": prov},
        "add_provider": {"provider_id": prov, "name": "hijacked", "executor": "api", "kind": "tool"},
        "update_provider": {"provider_id": prov, "name": "hijacked", "enabled": False},
        "remove_provider": {"provider_id": prov, "force": True},
    }


def test_every_command_kind_has_an_isolation_scenario() -> None:
    assert set(command_payloads({k: "x" for k in ("conn", "pid", "cap", "alert", "jid")})) == set(PAYLOAD_VALIDATORS)


@pytest.mark.parametrize("kind", sorted(PAYLOAD_VALIDATORS))
async def test_a_command_aimed_at_the_other_tenants_ids_changes_nothing_of_theirs(
    pool: DbPool, two_tenants: TwoTenants, kind: str
) -> None:
    a, b = two_tenants.a, two_tenants.b
    before_b = await snapshot(pool, b)
    before_a = await snapshot(pool, a)
    payload = command_payloads(two_tenants.params[b])[kind]
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "insert into public.farm_commands (kind, payload, created_by) values (%s, %s, 'iso') returning id",
            (kind, json.dumps(payload)),
        )
        row = await cur.fetchone()
    assert row is not None
    status, result = await process_command(pool, row[0])
    assert status in ("done", "rejected", "failed"), (kind, status, result)
    assert "iso-b-text" not in json.dumps(result, default=str)  # no field of B's rows is echoed back
    after_b = await snapshot(pool, b)
    assert after_b == before_b, (kind, status, result)
    # A's own rows may change (its command row), but never lose anything: B's rows were never A's to touch
    after_a = await snapshot(pool, a)
    assert after_a["providers"] == before_a["providers"] or status == "done", (kind, status, result)


async def test_the_command_consumer_runs_each_command_for_the_tenant_that_queued_it(
    pool: DbPool, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    conn_b = two_tenants.params[b]["conn"]
    with use_tenant(b):
        async with tenant_connection(pool) as conn:
            await conn.execute(
                "insert into public.farm_commands (kind, payload, created_by) values ('pause', %s, 'iso')",
                (json.dumps({"connection_id": conn_b}),),
            )
    before_a = await snapshot(pool, a)
    with use_tenant(a):  # the consumer is tenant-less; whoever calls it, B's command runs as B
        assert await poll_and_execute_queued(pool) == 3  # both seeded (payload-less, rejected) and B's real one
    async with system_connection(pool) as conn:
        status = await (await conn.execute("select status from public.connections where id = %s", (conn_b,))).fetchone()
        audit = await (
            await conn.execute("select tenant_id from public.audit_events where action = 'pause' and target = %s", (conn_b,))
        ).fetchone()
        done = await (await conn.execute("select tenant_id, status from public.farm_commands where created_by = 'iso'")).fetchall()
    assert status == ("paused",)
    assert audit == (b,)
    assert done == [(b, "done")]
    after_a = await snapshot(pool, a)  # nothing landed in A (its own seeded, payload-less command was rejected)
    assert {t: d for t, d in after_a.items() if t != "farm_commands"} == {
        t: d for t, d in before_a.items() if t != "farm_commands"
    }


# --- MCP tools ----------------------------------------------------------------------------------------------


async def test_every_mcp_tool_has_an_isolation_scenario(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    ctx = await farm_factory(registry)
    server = await build_server(ctx)
    async with Client(server) as client:
        listed = {t.name for t in await client.list_tools()}
    capability_tools = listed - set(INFRA_TOOLS) - set(AI_TOOLS)
    assert capability_tools, "no capability tools were generated"
    assert {"verify_email"} <= capability_tools
    # every capability tool is the same generated class, so the verify_email scenario below covers its path
    for name in capability_tools:
        assert type(await server.get_tool(name)).__name__ == "CapabilityTool", name
    assert set(INFRA_CASES) == set(INFRA_TOOLS)
    assert set(AI_CASES) == set(AI_TOOLS)


async def _text(result: Any) -> str:
    return json.dumps(result.structured_content, default=str) + str(result.content)


async def _expect_nothing_of_b(client: Client[Any], tool: str, args: dict[str, Any]) -> str:
    try:
        text = await _text(await client.call_tool(tool, args))
    except ToolError as exc:
        text = str(exc)
    for marker in ("iso-b-text", "iso-b-company", "iso-b-session"):  # B's data; a refusal may echo the id it was given
        assert marker not in text, (tool, text)
    return text


async def _infra_get_run(c: Client[Any], b: dict[str, Any]) -> None:
    with pytest.raises(ToolError, match="there is no run"):
        await c.call_tool("get_run", {"run_id": str(b["rid"])})


async def _infra_integration(c: Client[Any], b: dict[str, Any]) -> None:
    await _expect_nothing_of_b(c, "list_integration_requests", {})
    # B already asked for "iso-b-text": the same request from A is new, not a duplicate of B's
    created = await c.call_tool("request_integration", {"name": "iso-b-text", "kind": "mcp", "purpose": "x"})
    assert created.structured_content is not None and created.structured_content["deduplicated"] is False


type Case = Callable[[Client[Any], dict[str, Any]], Awaitable[None]]


def _plain(tool: str, args: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]) -> Case:
    """A scenario that calls ``tool`` as tenant A (``args`` may be built from B's ids) and expects nothing of B's."""

    async def case(c: Client[Any], b: dict[str, Any]) -> None:
        await _expect_nothing_of_b(c, tool, args(b) if callable(args) else args)

    return case


INFRA_CASES: dict[str, Case] = {
    "farm_guide": _plain("farm_guide", {}),
    "get_capacity": _plain("get_capacity", {}),
    "list_resources": _plain("list_resources", {}),
    "get_run": _infra_get_run,
    "get_usage": _plain("get_usage", {"days": 30}),
    "request_integration": _infra_integration,
    "list_integration_requests": _plain("list_integration_requests", {}),
}


AI_CASES: dict[str, Case] = {
    "list_ais": _plain("list_ais", {}),
    "ask_ai_batch": _plain("ask_ai_batch", lambda b: {"tasks": [{"task": "hi", "account": b["conn"]}]}),
    "ai_start": _plain("ai_start", lambda b: {"task": "hi", "ai": "claude", "account": b["conn"]}),
    "ai_start_many": _plain(
        "ai_start_many", lambda b: {"jobs": [{"task": "hi", "ai": "claude", "account": b["conn"]}]}
    ),
    "ai_status": _plain("ai_status", lambda b: {"job_ids": [str(b["jid"])]}),
    "ai_wait": _plain("ai_wait", lambda b: {"job_ids": [str(b["jid"])], "timeout_s": 0}),
    "ai_result": _plain("ai_result", lambda b: {"job_id": str(b["jid"])}),
    "ai_cancel": _plain("ai_cancel", lambda b: {"job_id": str(b["jid"])}),
    "ai_reply": _plain("ai_reply", lambda b: {"conversation_id": str(b["cv"]), "message": "hi"}),
    "ai_conversations": _plain("ai_conversations", {}),
}


@pytest.mark.parametrize("tool", sorted(set(INFRA_TOOLS) | set(AI_TOOLS)))
async def test_a_tool_called_as_a_never_shows_or_changes_bs_data(
    pool: DbPool, farm_factory: Make, registry: Registry, two_tenants: TwoTenants, tool: str
) -> None:
    ctx = await farm_factory(registry, executors={"api": ScriptedExecutor(lambda req: ok_result())})
    server = await build_server(ctx)
    before_b = await snapshot(pool, two_tenants.b)
    case = INFRA_CASES.get(tool) or AI_CASES[tool]
    with use_tenant(two_tenants.a):
        async with Client(server) as client:
            await case(client, two_tenants.params[two_tenants.b])
    assert await snapshot(pool, two_tenants.b) == before_b, tool


async def test_a_capability_call_is_recorded_for_its_tenant_and_invisible_to_the_other(
    pool: DbPool, farm_factory: Make, registry: Registry, two_tenants: TwoTenants
) -> None:
    ctx = await farm_factory(registry, executors={"api": ScriptedExecutor(lambda req: ok_result())})
    server = await build_server(ctx)
    with use_tenant(two_tenants.a):
        async with Client(server) as client:
            envelope = (await client.call_tool("verify_email", {"email": EMAIL})).structured_content
    assert envelope is not None
    run_id = envelope["run_id"]
    async with system_connection(pool) as conn:
        owner = await (await conn.execute("select tenant_id from public.runs where id = %s", (run_id,))).fetchone()
        events = await (
            await conn.execute("select distinct tenant_id from public.run_events where run_id = %s", (run_id,))
        ).fetchall()
    assert owner == (two_tenants.a,) and events == [(two_tenants.a,)]
    with use_tenant(two_tenants.b):
        async with Client(server) as client:
            with pytest.raises(ToolError, match="there is no run"):
                await client.call_tool("get_run", {"run_id": run_id})
            # B has no capabilities of its own: A's registry, cache and routes are not reachable from B
            with pytest.raises(ToolError, match="unknown capability"):
                await client.call_tool("verify_email", {"email": EMAIL})


async def test_an_http_request_runs_for_the_tenant_of_its_token(
    pool: DbPool, farm_factory: Make, registry: Registry, two_tenants: TwoTenants
) -> None:
    import httpx
    from fastmcp.client.transports import StreamableHttpTransport

    from farm.control.run import create_farm_app, create_token, revoke_token

    tokens: dict[UUID, str] = {}
    for tenant, name in ((two_tenants.a, "iso-a-client"), (two_tenants.b, "iso-b-client")):
        with use_tenant(tenant):
            tokens[tenant] = await create_token(pool, name)
    ctx = await farm_factory(registry, executors={"api": ScriptedExecutor(lambda req: ok_result())})
    app = await create_farm_app(ctx)

    def client_for(token: str) -> Client[Any]:
        def factory(headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                headers=headers,
                timeout=kwargs.get("timeout"),
                auth=kwargs.get("auth"),
            )

        return Client(
            StreamableHttpTransport(
                "http://127.0.0.1/mcp", headers={"Authorization": f"Bearer {token}"}, httpx_client_factory=factory
            )
        )

    base = getattr(app, "app", app)
    async with base.router.lifespan_context(base):
        async with client_for(tokens[two_tenants.a]) as client:
            envelope = (await client.call_tool("verify_email", {"email": EMAIL})).structured_content
            assert envelope is not None
        async with client_for(tokens[two_tenants.b]) as client:  # B's token: B's tenant, which has no capabilities
            with pytest.raises(ToolError, match="unknown capability"):
                await client.call_tool("verify_email", {"email": EMAIL})
            with pytest.raises(ToolError, match="there is no run"):
                await client.call_tool("get_run", {"run_id": envelope["run_id"]})
    async with system_connection(pool) as conn:
        owner = await (
            await conn.execute("select tenant_id, caller from public.runs where id = %s", (envelope["run_id"],))
        ).fetchone()
    assert owner == (two_tenants.a, "iso-a-client")
    with use_tenant(two_tenants.b):
        assert await revoke_token(pool, "iso-a-client") is False  # B cannot revoke A's token
