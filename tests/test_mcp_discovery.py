"""Tests for MCP capability discovery, search, and tool listing (GUIDE1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from fastmcp import Client, FastMCP

from farm.db.pool import DbPool
from tests.mcp_helpers import (
    WorldFactory,
    mcp_provider,
    mcp_registry,
    world_factory,
)


@pytest_asyncio.fixture
async def mcp_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path) as make:
        yield make


def make_30_tool_server(name: str = "fake-30") -> FastMCP:
    server = FastMCP(name)
    for i in range(29):
        tool_name = f"tool_{i:02d}"

        def _fn(n: str = tool_name):
            def handler() -> dict[str, Any]:
                return {"tool": n}

            return handler

        server.add_tool(
            FastMCP.tool(server, name=tool_name, description=f"Description for {tool_name}")(_fn(tool_name))
        )

    # 30th tool: get-credits-available
    @server.tool(name="get-credits-available", description="Get credits available in workspace")
    def get_credits_available() -> dict[str, Any]:
        return {"credits": 100}

    return server


async def test_30_tools_discovery_and_call(mcp_world: WorldFactory) -> None:
    remote = make_30_tool_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider(expose="discovery")), {"fake-01": remote})
    synced = await world.sync()
    assert [r.ok for r in synced] == [True]
    assert len(synced[0].tools) == 30

    gateway = await world.gateway()
    async with Client(gateway) as client:
        # Check call_tool with every tool
        for t in synced[0].tools:
            exposed_name = f"fake__{t}"
            res = await client.call_tool("call_tool", {"name": exposed_name, "arguments": {}})
            assert res is not None

        # Check searching
        search_res = await client.call_tool("search_tools", {"query": "get-credits-available"})
        print("\nSEARCH RESULT (expose=discovery):", search_res)

        # Try calling with un-prefixed name
        try:
            unprefixed_res = await client.call_tool(
                "call_tool", {"name": "get-credits-available", "arguments": {}}
            )
            print("UNPREFIXED CALL RESULT:", unprefixed_res)
        except Exception as e:
            print("UNPREFIXED CALL FAILED:", type(e), e)
