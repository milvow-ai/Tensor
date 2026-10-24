"""Q0A / D2: every pooled tool is callable through ``call_tool`` by its exact name and by each documented variant.

Live failure (2026-10-24): ``call_tool("clay__get-credits-available")`` answered "Unknown tool" although the tool
was stored. The real chain with only the servers faked: registry -> Postgres -> sync -> gateway -> MCP client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from farm.db.pool import DbPool
from tests.fake_mcp_tools_server import (
    APP_ONLY,
    HYPHEN,
    LISTED_TOOL_NAMES,
    MIXED,
    TOOL_NAMES,
    TWIN_HYPHEN,
    TWIN_UNDERSCORE,
    UNDERSCORE,
    create_fake_tools_server,
)
from tests.mcp_helpers import WorldFactory, mcp_provider, mcp_registry, world_factory


@pytest_asyncio.fixture
async def mcp_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path) as make:
        yield make


@pytest_asyncio.fixture
async def gateway(mcp_world: WorldFactory) -> FastMCP:
    """``lst`` lists its two tools directly; ``hid`` hides all five behind ``search_tools`` / ``call_tool``."""
    registry = mcp_registry(
        lst=mcp_provider(expose="direct"),
        hid=mcp_provider(expose="discovery"),
    )
    world = await mcp_world(
        registry,
        {
            "lst-01": create_fake_tools_server("lst", tools=LISTED_TOOL_NAMES),
            "hid-01": create_fake_tools_server("hid"),
        },
    )
    assert all(r.ok for r in await world.sync())
    return await world.gateway()


async def ran(client: Client[Any], name: str) -> str:
    """The name of the remote tool that ``call_tool(name)`` ran."""
    result = await client.call_tool("call_tool", {"name": name, "arguments": {}})
    assert result.structured_content is not None
    return str(result.structured_content["tool"])


@pytest.mark.parametrize("remote", TOOL_NAMES)
async def test_exact_name_of_a_discoverable_tool_is_callable(gateway: FastMCP, remote: str) -> None:
    async with Client(gateway) as client:
        assert await ran(client, f"hid__{remote}") == remote


@pytest.mark.parametrize("remote", LISTED_TOOL_NAMES)
async def test_exact_name_of_a_listed_tool_is_callable_both_ways(gateway: FastMCP, remote: str) -> None:
    async with Client(gateway) as client:
        listed = {tool.name for tool in await client.list_tools()}
        assert f"lst__{remote}" in listed
        assert await ran(client, f"lst__{remote}") == remote
        direct = await client.call_tool(f"lst__{remote}", {})
        assert direct.structured_content == {"tool": remote}


@pytest.mark.parametrize(
    ("asked", "remote"),
    [
        ("get-credits-available", HYPHEN),  # unprefixed
        ("get_credits_available", HYPHEN),  # unprefixed, other separator
        ("hid__get_credits_available", HYPHEN),  # prefixed, other separator
        ("hid:get-credits-available", HYPHEN),  # colon namespace
        ("hid/get-credits-available", HYPHEN),  # slash namespace
        ("hid:get_credits_available", HYPHEN),  # colon namespace and other separator
        ("HID__GET-CREDITS-AVAILABLE", HYPHEN),  # case
        ("list-subroutines", UNDERSCORE),
        ("hid__list-subroutines", UNDERSCORE),
        ("hid/list_subroutines", UNDERSCORE),
        ("get_task-context", MIXED),
        ("get-task-context", MIXED),
        ("hid__get_task_context", MIXED),
        ("hid:get_task-context", MIXED),
    ],
)
async def test_documented_variants_resolve_to_the_one_tool(gateway: FastMCP, asked: str, remote: str) -> None:
    async with Client(gateway) as client:
        assert await ran(client, asked) == remote


async def test_listed_tool_resolves_unprefixed_and_by_variant(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        assert await ran(client, "list-widgets") == "list-widgets"
        assert await ran(client, "list_widgets") == "list-widgets"
        assert await ran(client, "lst:get_widget") == "get_widget"


async def test_variant_matching_two_tools_is_an_error_naming_the_candidates(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        with pytest.raises(ToolError) as ambiguous:
            await client.call_tool("call_tool", {"name": "HID__GET_USER_ID", "arguments": {}})
        message = str(ambiguous.value)
        assert "ambiguous" in message.lower()
        assert f"hid__{TWIN_HYPHEN}" in message
        assert f"hid__{TWIN_UNDERSCORE}" in message


async def test_exact_name_stays_authoritative_when_a_twin_exists(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        assert await ran(client, f"hid__{TWIN_HYPHEN}") == TWIN_HYPHEN
        assert await ran(client, f"hid__{TWIN_UNDERSCORE}") == TWIN_UNDERSCORE
        assert await ran(client, f"hid:{TWIN_UNDERSCORE}") == TWIN_UNDERSCORE
        assert await ran(client, f"hid/{TWIN_HYPHEN}") == TWIN_HYPHEN


async def test_unknown_name_is_an_error(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("call_tool", {"name": "hid__nothing-here", "arguments": {}})


async def test_tool_declared_app_only_is_still_callable_and_searchable(gateway: FastMCP) -> None:
    """The live defect: a stored tool that declares ``ui.visibility = ["app"]`` was missing from the catalog."""
    async with Client(gateway) as client:
        assert await ran(client, f"hid__{APP_ONLY}") == APP_ONLY
        assert await ran(client, "hid:get_app_credits") == APP_ONLY
        found = await client.call_tool("search_tools", {"query": "credits shown by the app"})
        assert APP_ONLY in str(found.structured_content or found.content)
