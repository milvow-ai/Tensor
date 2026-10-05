"""M1 acceptance: Claude's view. A real MCP client calls ``verify_email`` on the real server and gets the envelope.

Everything between the client and the (mocked) provider is real: FastMCP server and middleware, router, ledger,
Postgres, adapters. The in-memory transport carries the actual MCP protocol messages.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import mcp_types
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.gateway.server import INFRA_TOOLS, build_server
from farm.registry import Registry
from tests.conftest import REOON_URL, ZEROBOUNCE_URL, reoon_body
from tests.farm_helpers import EMAIL, fetch, only_reoon_01

type Make = Callable[..., Awaitable[FarmContext]]

AI_JOB_TOOLS = {
    "ai_cancel",
    "ai_conversations",
    "ai_reply",
    "ai_result",
    "ai_start",
    "ai_start_many",
    "ai_status",
    "ai_wait",
}  # AIP2 (non-blocking AI jobs)


async def remaining_credits(client: Client[Any], connection_id: str) -> float:
    capacity = (await client.call_tool("get_capacity", {"capability": "verify_email"})).structured_content
    assert capacity is not None
    for pool in capacity["pools"]:
        for connection in pool["connections"]:
            if connection["id"] == connection_id:
                (credits,) = [u for u in connection["units"] if u["unit"] == "credits"]
                return float(credits["remaining"])
    raise AssertionError(f"{connection_id} is not in get_capacity")


async def test_claude_calls_verify_email_gets_the_envelope_reads_the_trajectory_and_sees_the_quota_drop(
    pool: DbPool,
    farm_factory: Make,
    registry: Registry,
    http: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    reoon = http.get(REOON_URL).respond(200, json=reoon_body("safe"))
    server = await build_server(ctx)

    async with Client(
        server, client_info=mcp_types.Implementation(name="claude-code", version="2.1")
    ) as client:
        names = {t.name for t in await client.list_tools()}
        # the AI tools are part of the surface now (the server no longer hides them from tests by test name)
        assert names == {
            "verify_email",
            "ask_ai",
            "ask_ai_batch",
            "list_ais",
            *AI_JOB_TOOLS,
            *INFRA_TOOLS,
        }  # no admin tool

        assert await remaining_credits(client, "reoon-01") == 20
        with caplog.at_level(logging.ERROR):
            reply = await client.call_tool("verify_email", {"email": EMAIL})
        assert not [r for r in caplog.records if "Error parsing structured content" in r.getMessage()]
        assert reply.data is not None  # the client could type the result with the tool's output schema

        envelope = reply.structured_content
        assert envelope is not None
        assert set(envelope) == {"ok", "result", "error", "run_id", "source", "cost"}
        assert envelope["ok"] is True and envelope["error"] is None
        assert envelope["result"]["email"] == EMAIL and envelope["result"]["status"] == "valid"
        assert envelope["result"]["provider"] == "reoon"
        assert envelope["source"] == {"provider": "reoon", "connection_id": "reoon-01", "cached": False}
        assert envelope["cost"] == {"usd": 0.0, "units": {"credits": 1.0}}
        UUID(envelope["run_id"])
        assert reoon.call_count == 1

        run = (await client.call_tool("get_run", {"run_id": envelope["run_id"]})).structured_content
        assert run is not None
        assert (run["capability"], run["status"], run["caller"], run["connection_id"]) == (
            "verify_email",
            "succeeded",
            "claude",  # the caller is taken from the client's declared name
            "reoon-01",
        )
        kinds = [e["kind"] for e in run["events"]]
        assert kinds[0] == "plan" and "reserve" in kinds and "execute" in kinds and "success" in kinds
        assert [e["seq"] for e in run["events"]] == list(range(1, len(kinds) + 1))

        assert await remaining_credits(client, "reoon-01") == 19  # the decrement is visible to the agent

        again = (await client.call_tool("verify_email", {"email": EMAIL})).structured_content
        assert (
            again is not None
            and again["source"]["cached"] is True
            and again["cost"] == {"usd": 0.0, "units": {}}
        )
        assert reoon.call_count == 1 and await remaining_credits(client, "reoon-01") == 19


async def test_a_failed_call_is_an_error_result_that_explains_itself(
    pool: DbPool, farm_factory: Make, registry: Registry, http: respx.MockRouter
) -> None:
    ctx = await farm_factory(only_reoon_01(registry))
    http.get(REOON_URL).respond(500, json={"status": "error", "reason": "internal error"})
    http.get(ZEROBOUNCE_URL).respond(500, json={"error": "internal error"})
    server = await build_server(ctx)

    async with Client(server) as client:
        result = await client.call_tool("verify_email", {"email": EMAIL}, raise_on_error=False)

    assert result.is_error  # MCP-level isError, so a client can branch on it...
    envelope = result.structured_content
    assert (
        envelope is not None and envelope["ok"] is False and envelope["result"] is None
    )  # ...and still read why
    error = envelope["error"]
    assert error["kind"] == "server" and error["hint"] and len(error["attempts"]) == 2
    assert envelope["source"]["connection_id"] == "zerobounce-01"  # where the last attempt went
    UUID(envelope["run_id"])


async def test_bad_input_is_an_envelope_not_a_protocol_error(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    ctx = await farm_factory(registry)
    server = await build_server(ctx)

    async with Client(server) as client:
        result = await client.call_tool("verify_email", {"email": "nope"}, raise_on_error=False)
        missing = await client.call_tool("verify_email", {}, raise_on_error=False)

    for r in (result, missing):
        assert r.is_error and r.structured_content is not None
        assert r.structured_content["error"]["kind"] == "bad_request"
    assert await fetch(pool, "select count(*) from quota_reservations") == [(0,)]


async def test_get_run_refuses_what_is_not_a_run(pool: DbPool, farm_ctx: FarmContext) -> None:
    server = await build_server(farm_ctx)
    async with Client(server) as client:
        with pytest.raises(ToolError, match="UUID"):
            await client.call_tool("get_run", {"run_id": "not-a-uuid"})
        with pytest.raises(ToolError, match="there is no run"):
            await client.call_tool("get_run", {"run_id": str(UUID(int=1))})
