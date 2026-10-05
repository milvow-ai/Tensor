"""OPEN1: the catalogue (``farm mcp sync``), the names tools are published under, and which are listed."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import mcp_types
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from structlog.testing import capture_logs

from farm.db.pool import DbPool
from farm.mcp.expose import is_allowed, plan_exposure
from farm.mcp.naming import exposed_names
from farm.mcp.store import McpProvider, StoredTool, tool_hash
from farm.registry.models import McpProviderSpec, McpToolsSpec
from tests.fake_mcp_server import create_fake_rich_server
from tests.farm_helpers import fetch
from tests.mcp_helpers import (
    WorldFactory,
    broken_transport,
    mcp_provider,
    mcp_registry,
    world_factory,
)


@pytest_asyncio.fixture
async def mcp_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path) as make:
        yield make


async def catalogue(pool: DbPool, provider: str = "fake") -> dict[str, str]:
    """Stored tool name -> schema hash."""
    rows = await fetch(pool, "select name, schema_hash from mcp_tools where provider = %s", provider)
    return dict(rows)


def small_server(name: str = "fake-01", *, echo_says: str = "Echo the message back.") -> FastMCP:
    server = FastMCP(name)

    @server.tool(name="echo", description=echo_says)
    def echo(message: str) -> str:
        return message

    @server.tool(name="stable")
    def stable() -> str:
        """Never changes."""
        return "same"

    return server


# --- sync ------------------------------------------------------------------------------------------------------


async def test_sync_stores_every_tool_of_a_server_that_paginates_with_its_whole_definition(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    remote = create_fake_rich_server("fake-01", page_size=2)  # 8 tools, 2 per page: needs 4 requests
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": remote})

    (result,) = await world.sync()

    assert result.ok and result.connection == "fake-01"
    assert result.tools == ("add", "echo", "rejected", "rich_echo", "schema_violation", "slow", "soft_error", "whoami")
    assert set(await catalogue(pool)) == set(result.tools)  # a client that ignored the cursors would have 2
    row = (
        await fetch(
            pool,
            "select description, input_schema, annotations, definition from mcp_tools "
            "where provider = 'fake' and name = 'echo'",
        )
    )[0]
    async with Client(remote) as direct:
        original = {t.name: t for t in await direct.list_tools()}["echo"]
    assert row[0] == original.description and row[1] == original.input_schema
    assert row[2] == original.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)  # type: ignore[union-attr]
    assert row[3] == original.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )  # title, _meta, ... too


async def test_the_deny_list_hides_a_tool_from_the_catalogue_the_listing_and_the_call(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider(deny=["soft_*", "slow"])), {"fake-01": remote})

    (result,) = await world.sync()

    assert result.denied == ("slow", "soft_error")
    assert set(await catalogue(pool)) == {"add", "echo", "rejected", "rich_echo", "schema_violation", "whoami"}
    gateway = await world.gateway()
    async with Client(gateway) as client:
        listed = {t.name for t in await client.list_tools()}
        with pytest.raises(ToolError, match="Unknown tool"):
            await client.call_tool("fake__soft_error", {"detail": "x"})
    assert "fake__echo" in listed and not {"fake__soft_error", "fake__slow"} & listed
    assert remote.state.calls == []  # type: ignore[attr-defined]  # the denied tool was never reached


async def test_a_deny_added_after_the_sync_applies_at_the_next_start_without_a_new_sync(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": create_fake_rich_server()})
    await world.sync()
    # the owner edits the registry: tools.deny gains whoami (registry sync only; no new tool listing)
    from farm.registry.sync import sync_registry

    await sync_registry(pool, mcp_registry(fake=mcp_provider(deny=["whoami"])))
    gateway = await world.gateway()

    async with Client(gateway) as client:
        listed = {t.name for t in await client.list_tools()}

    assert "fake__whoami" not in listed and "fake__echo" in listed


async def test_an_allow_list_limits_the_catalogue_to_the_matching_tools(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    world = await mcp_world(
        mcp_registry(fake=mcp_provider(allow=["echo", "add"])), {"fake-01": create_fake_rich_server()}
    )

    await world.sync()

    assert set(await catalogue(pool)) == {"echo", "add"}


async def test_a_changed_definition_gets_a_new_hash_and_the_change_is_logged(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": small_server()})
    (first,) = await world.sync()
    before = await catalogue(pool)
    assert first.diff is not None and first.diff.added == ("echo", "stable")

    await world.use("fake-01", small_server(echo_says="Echo it, now with a new description."))
    with capture_logs() as logs:
        (second,) = await world.sync()
    after = await catalogue(pool)

    assert after["echo"] != before["echo"] and after["stable"] == before["stable"]
    assert second.diff is not None and second.diff.unchanged == ("stable",)
    assert second.diff.changed == (("echo", before["echo"], after["echo"]),)
    changes = [entry for entry in logs if entry["event"] == "mcp.schema_changed"]
    assert [(c["provider"], c["tool"], c["old_hash"], c["new_hash"]) for c in changes] == [
        ("fake", "echo", before["echo"], after["echo"])
    ]


async def test_a_tool_the_server_no_longer_lists_is_removed_from_the_catalogue(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": small_server()})
    await world.sync()
    only_echo = FastMCP("fake-01")

    @only_echo.tool(name="echo")
    def echo(message: str) -> str:
        """Echo the message back."""
        return message

    await world.use("fake-01", only_echo)
    (result,) = await world.sync()

    assert set(await catalogue(pool)) == {"echo"}
    assert result.diff is not None and result.diff.removed == ("stable",)


async def test_syncing_twice_changes_nothing_but_the_sync_time(mcp_world: WorldFactory, pool: DbPool) -> None:
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": create_fake_rich_server()})
    await world.sync()
    first = await fetch(pool, "select name, schema_hash, synced_at from mcp_tools order by name")

    (second,) = await world.sync()
    again = await fetch(pool, "select name, schema_hash, synced_at from mcp_tools order by name")

    assert second.diff is not None and not (second.diff.added or second.diff.changed or second.diff.removed)
    assert [r[:2] for r in again] == [r[:2] for r in first] and all(
        a[2] > f[2] for a, f in zip(again, first, strict=True)
    )


async def test_a_server_that_cannot_start_marks_its_accounts_unhealthy_and_keeps_the_catalogue(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    known = await catalogue(pool)
    await world.use("fake-01", broken_transport())
    await world.use("fake-02", broken_transport())

    (result,) = await world.sync()

    assert not result.ok and result.connection is None and result.error is not None
    assert "fake-01: server" in result.error and "fake-02: server" in result.error
    assert await catalogue(pool) == known  # the last known catalogue stays: serving continues from it
    health = await fetch(
        pool,
        "select connection_id, last_error_kind, failure_count from connection_health "
        "where failure_count > 0 order by connection_id",
    )
    assert health == [("fake-01", "server", 1), ("fake-02", "server", 1)]


async def test_a_provider_that_fails_does_not_affect_the_others(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    registry = mcp_registry(dead=mcp_provider(), good=mcp_provider())
    world = await mcp_world(
        registry, {"dead-01": broken_transport(), "good-01": create_fake_rich_server("good-01")}
    )

    results = {r.provider: r for r in await world.sync()}

    assert not results["dead"].ok and results["good"].ok
    assert await catalogue(pool, "good") and not await catalogue(pool, "dead")


async def test_sync_skips_the_accounts_the_router_would_skip(mcp_world: WorldFactory, pool: DbPool) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    async with pool.connection() as conn:
        await conn.execute("update connections set status = 'paused' where id = 'fake-01'")

    (result,) = await world.sync()
    assert result.ok and result.connection == "fake-02"

    async with pool.connection() as conn:
        await conn.execute("update connections set status = 'needs_login' where id = 'fake-02'")
    (blocked,) = await world.sync()
    assert not blocked.ok and blocked.error == "fake-01: skipped (paused); fake-02: skipped (needs_login)"


async def test_a_disabled_provider_is_not_synced_and_a_named_one_that_does_not_exist_is_empty(
    mcp_world: WorldFactory,
) -> None:
    world = await mcp_world(
        mcp_registry(fake=mcp_provider(), off={**mcp_provider(), "enabled": False}),
        {"fake-01": create_fake_rich_server(), "off-01": create_fake_rich_server()},
    )

    assert [r.provider for r in await world.sync()] == ["fake"]
    assert await world.sync("nope") == []


async def test_the_pass_through_capability_has_no_strategy_of_its_own_so_the_provider_decides(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    world = await mcp_world(
        mcp_registry(fake=mcp_provider(strategy="round_robin")), {"fake-01": create_fake_rich_server()}
    )

    await world.sync()

    assert await fetch(
        pool, "select kind, default_strategy, cache_ttl_seconds from capabilities where name = 'mcp:fake'"
    ) == [("tool", None, 0)]
    assert await fetch(
        pool, "select provider_id, enabled from capability_routes where capability = 'mcp:fake'"
    ) == [("fake", True)]


# --- names -----------------------------------------------------------------------------------------------------


def test_names_are_namespace_double_underscore_tool_and_valid_everywhere() -> None:
    names = exposed_names("notion", ["search", "create-page", "search.pages", "über tool"])

    assert names == {
        "search": "notion__search",
        "create-page": "notion__create-page",
        "search.pages": "notion__search_pages",
        "über tool": "notion___ber_tool",
    }


def test_a_name_that_is_too_long_is_cut_and_tagged_with_a_stable_hash() -> None:
    long = "a" * 80

    first = exposed_names("github", [long, "short"])
    again = exposed_names("github", ["short", long])

    assert first == again  # independent of the order the server listed them in
    assert len(first[long]) == 64 and first[long].startswith("github__aaaa")
    assert (
        first[long] != exposed_names("github", ["a" * 79 + "b"])["a" * 79 + "b"]
    )  # distinct tools stay distinct


def test_two_tools_that_sanitise_to_the_same_name_both_get_a_hash() -> None:
    names = exposed_names("ns", ["a.b", "a_b"])

    assert len(set(names.values())) == 2 and all(n.startswith("ns__a_b_") for n in names.values())


# --- which tools are listed ------------------------------------------------------------------------------------


def provider(provider_id: str, expose: str, deny: tuple[str, ...] = ()) -> McpProvider:
    spec = McpProviderSpec(command="x", expose=expose, tools=McpToolsSpec(deny=list(deny)))  # type: ignore[arg-type]
    return McpProvider(provider_id, provider_id, True, spec)


def tools(provider_id: str, *names: str) -> list[StoredTool]:
    definitions = [mcp_types.Tool(name=n, input_schema={"type": "object"}) for n in names]
    return [StoredTool(provider_id, d, tool_hash(d), None) for d in definitions]  # type: ignore[arg-type]


def listed(plan: list[Any]) -> list[str]:
    return [item.name for item in plan if item.direct]


def test_direct_lists_everything_and_discovery_lists_nothing() -> None:
    plan = plan_exposure(
        [provider("a", "direct"), provider("b", "discovery")],
        tools("a", "x", "y") + tools("b", "z"),
        direct_limit=1,
    )

    assert listed(plan) == ["a__x", "a__y"] and [i.name for i in plan] == ["a__x", "a__y", "b__z"]


def test_auto_lists_everything_up_to_the_limit_and_then_only_the_pinned_tools() -> None:
    providers = [provider("a", "auto"), provider("b", "direct")]
    stored = tools("a", "x", "y") + tools("b", "z")

    assert listed(plan_exposure(providers, stored, direct_limit=3)) == ["a__x", "a__y", "b__z"]
    over = plan_exposure(providers, stored, direct_limit=2, pinned=["a__y"])
    assert listed(over) == ["a__y", "b__z"]  # a is on discovery but keeps its pinned tool; b is direct anyway
    assert listed(plan_exposure(providers, stored, direct_limit=2, pinned=["a__*"])) == [
        "a__x",
        "a__y",
        "b__z",
    ]


def test_deny_and_allow_are_case_sensitive_globs() -> None:
    spec = McpToolsSpec(allow=["get_*", "list_*"], deny=["*_secret"])  # type: ignore[arg-type]

    assert [n for n in ("get_x", "list_y", "get_secret", "Get_x", "delete_x") if is_allowed(n, spec)] == [
        "get_x",
        "list_y",
    ]


# --- discovery through the gateway -------------------------------------------------------------------------


async def test_beyond_the_limit_only_pinned_tools_are_listed_and_the_rest_is_found_by_search(
    mcp_world: WorldFactory,
) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider(expose="auto")), {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway(mcp_direct_limit=3, mcp_pinned=["fake__echo"])

    async with Client(gateway) as client:
        listed_names = {t.name for t in await client.list_tools()}
        found = await client.call_tool("search_tools", {"query": "add two integers"})
        ran = await client.call_tool("call_tool", {"name": "fake__add", "arguments": {"a": 2, "b": 40}})

    assert {"fake__echo", "search_tools", "call_tool"} <= listed_names
    assert not {n for n in listed_names if n.startswith("fake__")} - {"fake__echo"}  # the rest is hidden...
    assert [t["name"] for t in found.structured_content["result"]][0] == "fake__add"  # type: ignore[index]  # ...but found
    assert ran.structured_content == {"result": 42}  # ...and callable, with the server's own answer
