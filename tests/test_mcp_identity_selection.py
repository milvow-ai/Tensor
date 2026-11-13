"""Identity-bound MCP connections cannot silently substitute another person's account."""

from pathlib import Path

from fastmcp import Client

from farm.db.pool import DbPool
from tests.fake_mcp_server import create_fake_rich_server
from tests.mcp_helpers import broken_transport, mcp_provider, mcp_registry, world_factory


async def test_identity_pool_requires_choice_and_discovery_provides_connection_ids(
    pool: DbPool, tmp_path: Path
) -> None:
    servers = {f"fake-0{n}": create_fake_rich_server(f"fake-0{n}") for n in (1, 2)}
    async with world_factory(pool, tmp_path) as make:
        world = await make(mcp_registry(fake=mcp_provider(accounts=2, pool_group=None)), servers)
        await world.sync()
        gateway = await world.gateway()
        async with Client(gateway) as client:
            refused = await client.call_tool_mcp("fake__whoami", {})
            assert refused.is_error and "account_selection_required" in refused.content[0].text
            connections = await client.call_tool("list_mcp_connections", {})
            assert "fake-01" in connections.content[0].text and "fake-02" in connections.content[0].text
            chosen = await client.call_tool_mcp("fake__whoami", {"_farm": {"account": "fake-02"}})
            assert not chosen.is_error and chosen.content[0].text == "fake-02"


async def test_pinned_unavailable_identity_never_rotates_to_another(pool: DbPool, tmp_path: Path) -> None:
    servers = {f"fake-0{n}": create_fake_rich_server(f"fake-0{n}") for n in (1, 2)}
    async with world_factory(pool, tmp_path) as make:
        world = await make(mcp_registry(fake=mcp_provider(accounts=2, pool_group=None)), servers)
        await world.sync()
        await world.use("fake-01", broken_transport())
        gateway = await world.gateway()
        async with Client(gateway) as client:
            refused = await client.call_tool_mcp("fake__whoami", {"_farm": {"account": "fake-01"}})
            assert refused.is_error
            assert not servers["fake-02"].state.calls
