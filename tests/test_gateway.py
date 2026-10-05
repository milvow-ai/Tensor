"""Gateway pieces: tool generation, middleware, and what the read-only tools report."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import mcp_types
import pytest
import respx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import McpError, ToolError

from farm.capabilities.schemas import CAPABILITY_MODELS
from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ErrorKind
from farm.gateway.middleware import caller_from_client_name
from farm.gateway.server import INFRA_TOOLS, build_server
from farm.registry import CapabilitySpec, Registry, load_registry
from tests.conftest import REOON_URL, START, FakeClock, zerobounce_body
from tests.farm_helpers import EMAIL, ScriptedExecutor, failure, fetch, ok_result, only_reoon_01

type Make = Callable[..., Awaitable[FarmContext]]

REAL_REGISTRY = Path(__file__).parent.parent / "config" / "registry.example.yaml"


@pytest.mark.parametrize(
    ("declared", "caller"),
    [
        ("claude-code", "claude"),
        ("Claude Desktop", "claude"),
        ("hermes", "hermes"),
        ("hermes-agent/0.21", "hermes"),
        ("my-script", "mcp:my-script"),
        ("  Weird  Client!! ", "mcp:weird-client"),
        ("", "mcp"),
        (None, "mcp"),
        ("x" * 200, "mcp:" + "x" * 48),
    ],
)
def test_the_caller_is_derived_from_the_clients_declared_name(declared: str | None, caller: str) -> None:
    assert caller_from_client_name(declared) == caller


async def test_a_tool_is_generated_for_every_capability_that_has_models_and_only_those(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.capabilities["find_email"] = CapabilitySpec(
        kind="tool", routes=["reoon"], description="Find an email."
    )
    registry.capabilities["unmodelled"] = CapabilitySpec(
        kind="tool", routes=["reoon"], description="A capability nobody wrote models for."
    )
    ctx = await farm_factory(registry)  # ask_ai has models (M3e); "unmodelled" has none
    server = await build_server(ctx)

    async with Client(server) as client:
        tools = {t.name: t for t in await client.list_tools()}

    assert set(tools) == {  # generated, not hand-written
        "verify_email",
        "find_email",
        "ask_ai",
        "ask_ai_batch",
        "list_ais",
        "ai_cancel",
        "ai_conversations",
        "ai_reply",
        "ai_result",
        "ai_start",
        "ai_start_many",
        "ai_status",
        "ai_wait",
        *INFRA_TOOLS,
    }
    assert "unmodelled" not in tools
    schema = tools["find_email"].input_schema
    assert schema["properties"]["routing_strategy"]["enum"]
    assert set(schema["properties"]) > {"routing_strategy"} and schema.get("required")  # plus the model's own
    assert tools["verify_email"].input_schema["required"] == ["email"]
    assert "Find an email." in (tools["find_email"].description or "")


async def test_every_capability_of_the_real_registry_gets_a_working_tool_schema(
    pool: DbPool, farm_factory: Make
) -> None:
    """The server must start on config/registry.yaml: no capability's own inputs may clash with the gateway's."""
    ctx = await farm_factory(load_registry(REAL_REGISTRY))
    server = await build_server(ctx)

    async with Client(server) as client:
        tools = {t.name: t for t in await client.list_tools()}

    with_models = set(CAPABILITY_MODELS) & set(load_registry(REAL_REGISTRY).capabilities)
    assert with_models <= set(tools) and len(with_models) >= 5
    for name in with_models:
        properties = tools[name].input_schema["properties"]
        assert "routing_strategy" in properties
        assert set(CAPABILITY_MODELS[name][0].model_fields) <= set(
            properties
        )  # the capability's own inputs kept
    assert (
        "strategy" in tools["pagespeed"].input_schema["properties"]
    )  # PageSpeed's mobile/desktop, untouched


async def test_the_strategy_argument_reaches_the_router(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor()})
    server = await build_server(ctx)

    async with Client(server) as client:
        envelope = (
            await client.call_tool("verify_email", {"email": EMAIL, "routing_strategy": "round_robin"})
        ).structured_content

    assert envelope is not None
    plan = await fetch(
        pool, "select data from run_events where run_id = %s and kind = 'plan'", envelope["run_id"]
    )
    assert plan[0][0]["requested_strategy"] == "round_robin"


async def test_a_policy_refusal_stops_the_call_before_anything_runs(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor()
    ctx = await farm_factory(registry, executors={"api": executor})
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def policy(caller: str, tool: str, arguments: dict[str, Any]) -> str | None:
        seen.append((caller, tool, arguments))
        return "hermes may not verify emails" if caller == "hermes" else None

    server = await build_server(ctx, policy=policy)

    async with Client(server, client_info=mcp_types.Implementation(name="hermes", version="1")) as client:
        with pytest.raises(ToolError, match="policy: hermes may not verify emails"):
            await client.call_tool("verify_email", {"email": EMAIL})
    async with Client(
        server, client_info=mcp_types.Implementation(name="claude-code", version="1")
    ) as client:
        allowed = await client.call_tool("verify_email", {"email": EMAIL})

    assert allowed.structured_content is not None and allowed.structured_content["ok"] is True
    assert [s[:2] for s in seen] == [("hermes", "verify_email"), ("claude", "verify_email")]
    assert len(executor.calls) == 1  # only the allowed call reached a provider
    assert await fetch(pool, "select count(*) from runs") == [(1,)]  # the refused call left no run


async def test_over_http_every_request_needs_the_bearer_token(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    ctx = await farm_factory(registry)
    server = await build_server(ctx, token="the-farm-token")
    app = server.http_app()

    def factory(
        headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None, **_: Any
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    def client_with(headers: dict[str, str]) -> Client[Any]:
        return Client(
            StreamableHttpTransport("http://127.0.0.1/mcp", headers=headers, httpx_client_factory=factory)
        )

    async with app.router.lifespan_context(app):
        async with client_with({"Authorization": "Bearer the-farm-token"}) as client:
            assert (await client.call_tool("get_usage", {})).structured_content is not None
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic the-farm-token"}):
            with pytest.raises(McpError, match="unauthorized"):
                async with client_with(headers) as client:
                    await client.call_tool("get_usage", {})


async def test_over_http_without_a_configured_token_nothing_is_served(
    pool: DbPool, farm_ctx: FarmContext
) -> None:
    server = await build_server(farm_ctx)  # no token: fine for stdio, never for HTTP
    app = server.http_app()

    def factory(
        headers: dict[str, str] | None = None, timeout: Any = None, auth: Any = None, **_: Any
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    async with app.router.lifespan_context(app):
        transport = StreamableHttpTransport(
            "http://127.0.0.1/mcp", headers={"Authorization": "Bearer anything"}, httpx_client_factory=factory
        )
        with pytest.raises(McpError, match="unauthorized"):
            async with Client(transport) as client:
                await client.call_tool("get_usage", {})


async def test_get_capacity_shows_state_remaining_and_the_next_reset(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    clock = FakeClock()
    ctx = await farm_factory(only_reoon_01(registry), clock=clock)
    http.get(REOON_URL).respond(
        429, headers={"Retry-After": "90"}, json={"status": "error", "reason": "slow down"}
    )
    http.get("https://api.zerobounce.net/v2/validate").respond(200, json=zerobounce_body("valid"))
    server = await build_server(ctx)

    async with Client(server) as client:
        await client.call_tool("verify_email", {"email": EMAIL})
        capacity = (await client.call_tool("get_capacity", {"capability": "verify_email"})).structured_content
        everything = (await client.call_tool("get_capacity", {})).structured_content

    assert capacity is not None and everything is not None
    assert [p["provider_id"] for p in capacity["pools"]] == ["reoon", "zerobounce"]  # route order
    assert {p["provider_id"] for p in everything["pools"]} == {"reoon", "zerobounce", "clay", "claude"}
    reoon, zerobounce = (p["connections"][0] for p in capacity["pools"])
    assert (reoon["available"], reoon["unavailable_reason"], reoon["last_error_kind"]) == (
        False,
        "cooldown",
        "rate_limited",
    )
    assert reoon["cooldown_until"].startswith((START + timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%S"))
    (credits,) = reoon["units"]
    assert (credits["limit"], credits["used"], credits["remaining"], credits["period"]) == (
        20.0,
        0.0,
        20.0,
        "day",
    )
    assert credits["next_reset_at"].startswith("2026-10-05T00:00:00")  # daily, UTC
    assert zerobounce["available"] is True
    (zb_credits,) = zerobounce["units"]
    assert (zb_credits["used"], zb_credits["remaining"], zb_credits["period"]) == (1.0, 99.0, "month")
    assert zb_credits["next_reset_at"].startswith("2026-11-01T00:00:00")  # anchor day 1


async def test_list_resources_describes_the_farm_without_any_credentials_reference(
    pool: DbPool, farm_ctx: FarmContext
) -> None:
    server = await build_server(farm_ctx)
    async with Client(server) as client:
        inventory = (await client.call_tool("list_resources", {})).structured_content

    assert inventory is not None
    capabilities = {c["name"]: c for c in inventory["capabilities"]}
    assert capabilities["verify_email"]["routes"] == ["reoon", "zerobounce"]
    providers = {p["id"]: p for p in inventory["providers"]}
    assert [c["id"] for c in providers["reoon"]["connections"]] == ["reoon-01", "reoon-02"]
    assert providers["reoon"]["connections"][0]["units"] == [
        {"unit": "credits", "limit": 20.0, "period": "day", "charged_on": "success"}
    ]
    assert "auth_ref" not in json.dumps(inventory) and "env:" not in json.dumps(inventory)


async def test_get_usage_adds_up_spend_and_runs(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    registry.budgets.per_provider.pop("reoon")
    registry.providers["reoon"].connections[0].units["credits"].unit_cost_usd = 0.25  # type: ignore[assignment]
    ctx = await farm_factory(
        only_reoon_01(registry),
        executors={
            "api": ScriptedExecutor(
                lambda r: ok_result() if r.params["email"].startswith("a") else failure(ErrorKind.UNKNOWN)
            )
        },
    )
    server = await build_server(ctx)

    async with Client(server) as client:
        await client.call_tool("verify_email", {"email": "a1@example.com"})
        await client.call_tool("verify_email", {"email": "a1@example.com"})  # cache hit
        await client.call_tool(
            "verify_email", {"email": "b1@example.com"}, raise_on_error=False
        )  # fails everywhere
        usage = (await client.call_tool("get_usage", {"days": 7})).structured_content

    assert usage is not None and usage["days"] == 7
    assert usage["total_cost_usd"] == pytest.approx(0.25)
    assert usage["by_connection"] == [
        {"connection_id": "reoon-01", "unit": "credits", "amount": 1.0, "cost_usd": 0.25, "events": 1}
    ]
    assert usage["runs"] == [
        {"capability": "verify_email", "status": "failed", "count": 1, "cost_usd": 0.0, "cached_runs": 0},
        {"capability": "verify_email", "status": "succeeded", "count": 2, "cost_usd": 0.25, "cached_runs": 1},
    ]


async def test_get_usage_rejects_a_silly_window(pool: DbPool, farm_ctx: FarmContext) -> None:
    server = await build_server(farm_ctx)
    async with Client(server) as client:
        for days in (0, 400):
            with pytest.raises(ToolError):
                await client.call_tool("get_usage", {"days": days})
