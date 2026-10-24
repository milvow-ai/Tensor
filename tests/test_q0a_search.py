"""Q0A / D3: ``search_tools`` returns compact hits, ``describe_tool`` returns the full definition.

Live failure (2026-10-24): one ``search_tools`` call returned 25 tools as ``list_tools`` definitions, 161,435
characters. Here a 40-tool server whose tools carry real-sized input schemas and long descriptions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from mcp_types import ToolAnnotations

from farm.db.pool import DbPool
from farm.gateway.mcp_tools import DESCRIPTION_LIMIT, one_line
from tests.mcp_helpers import WorldFactory, mcp_provider, mcp_registry, world_factory

MEDIUM = "Search the workspace records that match the filters and return one page of them with their credit cost."
LONG = (
    "Searches the workspace for matching records and returns them page by page, honouring every filter, sort "
    "order and field mask the caller passes, and reports how many credits the search used so far today."
)


@pytest_asyncio.fixture
async def mcp_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path) as make:
        yield make


def make_wide_server() -> FastMCP:
    """40 ``search-<n>`` tools with a long description and a 12-property schema, plus two tools of a kind."""
    server = FastMCP("wide")
    properties = {f"field_{n}": {"type": "string", "description": f"The filter number {n}."} for n in range(12)}
    for n in range(40):

        def handler() -> dict[str, Any]:
            return {}

        tool = Tool.from_function(
            handler,
            name=f"search-records-{n:02d}",
            description=f"{MEDIUM} Variant {n}.",
            annotations=ToolAnnotations(readOnlyHint=True),
        )
        server.add_tool(tool.model_copy(update={"parameters": {"type": "object", "properties": properties}}))

    @server.tool(name="explain-everything", description=LONG)
    def explain_everything() -> dict[str, Any]:
        return {}

    @server.tool(name="add-company-data-points", description="Add enrichment data points to a company.")
    def add_company_data_points(domain: str) -> dict[str, Any]:
        return {"domain": domain}

    @server.tool(name="delete-row", description="Delete one row from a table.")
    def delete_row(row: str) -> dict[str, Any]:
        return {"row": row}

    return server


@pytest_asyncio.fixture
async def gateway(mcp_world: WorldFactory) -> FastMCP:
    world = await mcp_world(
        mcp_registry(clay=mcp_provider(expose="discovery")), {"clay-01": make_wide_server()}
    )
    assert all(r.ok for r in await world.sync())
    return await world.gateway()


async def search(client: Client[Any], query: str, **arguments: Any) -> tuple[list[dict[str, Any]], int]:
    result = await client.call_tool("search_tools", {"query": query, **arguments})
    assert result.structured_content is not None
    assert result.content[0].type == "text"
    return list(result.structured_content["result"]), len(result.content[0].text)


async def test_25_hits_fit_in_6000_characters_and_carry_no_schema(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        hits, size = await search(client, "search records workspace filter", limit=25)
    assert len(hits) == 25
    assert size <= 6000, size
    for hit in hits:
        assert set(hit) == {"name", "server", "description", "risk", "score"}
        assert hit["server"] == "clay"
        assert hit["name"].startswith("clay__search-records-")
        assert hit["risk"] == "read-only"
        assert len(hit["description"]) <= DESCRIPTION_LIMIT
        assert hit["score"] > 0
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


async def test_default_is_10_and_the_maximum_is_25(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        assert len((await search(client, "search records"))[0]) == 10
        assert len((await search(client, "search records", limit=500))[0]) == 25
        assert len((await search(client, "search records", limit=3))[0]) == 3


async def test_risk_labels_follow_the_existing_rules(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        spend, _ = await search(client, "enrichment data points company")
        change, _ = await search(client, "delete row table")
    assert spend[0]["name"] == "clay__add-company-data-points"
    assert spend[0]["risk"] == "may-spend"
    assert change[0]["name"] == "clay__delete-row"
    assert change[0]["risk"] == "changes-data"


async def test_describe_tool_returns_the_exact_schema_of_the_server(gateway: FastMCP) -> None:
    remote = make_wide_server()
    async with Client(remote) as remote_client:
        original = next(t for t in await remote_client.list_tools() if t.name == "search-records-07")
    async with Client(gateway) as client:
        definition = (await client.call_tool("describe_tool", {"name": "clay__search-records-07"})).structured_content
        variant = (await client.call_tool("describe_tool", {"name": "search_records_07"})).structured_content
    assert definition is not None
    assert definition["inputSchema"] == original.inputSchema
    assert definition["annotations"]["readOnlyHint"] is True
    assert definition["name"] == "clay__search-records-07"
    assert variant == definition


async def test_a_long_description_is_cut_in_the_hit_and_complete_in_describe_tool(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        hits, _ = await search(client, "explain everything credits caller passes")
        top = next(h for h in hits if h["name"] == "clay__explain-everything")
        full = (await client.call_tool("describe_tool", {"name": top["name"]})).structured_content
    assert len(top["description"]) <= DESCRIPTION_LIMIT
    assert top["description"].endswith("…")
    assert full is not None
    assert full["description"] == LONG


async def test_describe_tool_of_an_unknown_name_is_an_error(gateway: FastMCP) -> None:
    async with Client(gateway) as client:
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("describe_tool", {"name": "clay__nothing"})


def test_one_line_cuts_at_a_word_boundary_within_the_limit() -> None:
    short = one_line("  Get the\ncredits   available.  ")
    assert short == "Get the credits available."
    cut = one_line(LONG)
    assert len(cut) <= DESCRIPTION_LIMIT
    assert cut.endswith("\u2026")
    assert LONG.startswith(cut[:-1])
    assert LONG[len(cut) - 1] == " "  # the cut fell on a space, not inside a word
    assert one_line(None) == ""
    assert len(one_line("x" * 500)) == DESCRIPTION_LIMIT
