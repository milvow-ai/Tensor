"""Pass-through (OPEN1): any MCP server's tools through the Farm, exactly as the server describes and answers.

The real chain except the servers: registry -> Postgres -> executor (relay client, error classification) ->
router -> gateway -> a real MCP client. See ``tests/mcp_helpers.py``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import mcp_types
import psutil
import pytest
import pytest_asyncio
from fastmcp import Client, FastMCP
from fastmcp.client.transports import SSETransport, StdioTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError
from key_value.aio.stores.filetree import FileTreeStore
from mcp.shared.exceptions import MCPError
from psycopg.types.json import Jsonb

from farm.db.pool import DbPool
from farm.executors.base import ConnectionView, ErrorKind
from farm.executors.mcp import relay
from farm.executors.mcp.connect import (
    HeadlessOAuth,
    LoginRequired,
    McpFailure,
    build_client,
    build_transport,
)
from farm.registry.models import McpProviderSpec
from farm.resources.router import route
from tests.fake_mcp_server import FAKE_PNG_B64, create_fake_rich_server
from tests.farm_helpers import events, fetch
from tests.mcp_helpers import (
    WorldFactory,
    broken_transport,
    comparable,
    database_text,
    mcp_provider,
    mcp_registry,
    world_factory,
)


@pytest_asyncio.fixture
async def mcp_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path) as make:
        yield make


def _raw(tool: mcp_types.Tool) -> dict[str, Any]:
    dumped: dict[str, Any] = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


async def _direct(server: FastMCP, tool: str, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    async with Client(server) as client:
        return await client.call_tool_mcp(tool, arguments)


async def test_a_listed_tool_is_the_servers_own_definition_byte_for_byte(mcp_world: WorldFactory) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": remote})
    assert [r.ok for r in await world.sync()] == [True]
    gateway = await world.gateway()

    async with Client(gateway) as client, Client(remote) as direct:
        listed = {t.name: t for t in await client.list_tools()}
        original = {t.name: t for t in await direct.list_tools()}

    assert {f"fake__{name}" for name in original} <= set(listed)
    for name, tool in original.items():
        mine = listed[f"fake__{name}"]
        # title, description, input and output schema, annotations, icons and _meta: the server's, unchanged
        assert _raw(mine) == _raw(tool) | {"name": f"fake__{name}"}


async def test_every_kind_of_answer_comes_back_exactly_as_the_server_sent_it(mcp_world: WorldFactory) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        for tool, arguments in (
            ("rich_echo", {"message": "hello"}),
            ("echo", {"message": "plain"}),
            ("add", {"a": 2, "b": 3}),
            ("soft_error", {"detail": "boom"}),
        ):
            through_farm = await client.call_tool_mcp(f"fake__{tool}", arguments)
            direct = await _direct(remote, tool, arguments)
            assert comparable(through_farm) == comparable(direct), tool

        rich = await client.call_tool_mcp("fake__rich_echo", {"message": "x"})
    kinds = [block.type for block in rich.content]
    assert kinds == ["text", "image", "audio", "resource", "resource_link"]
    assert rich.content[1].data == FAKE_PNG_B64  # the image, byte for byte
    assert rich.structured_content == {"message": "x", "blocks": 5}


async def test_a_tool_that_reports_iserror_is_a_result_not_a_failure(mcp_world: WorldFactory) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        result = await client.call_tool_mcp("fake__soft_error", {"detail": "nope"})

    assert result.is_error is True and result.content[0].text == "remote failure: nope"  # type: ignore[union-attr]
    assert result.structured_content == {"detail": "nope"}
    answered = [s for s in servers.values() if s.state.calls]  # type: ignore[attr-defined]
    assert len(answered) == 1  # no failover to the second account
    runs = await fetch(world.ctx.pool, "select status, error_kind from runs where capability = 'mcp:fake'")
    assert runs == [("succeeded", None)]


async def test_the_server_never_sees_the_farm_argument(mcp_world: WorldFactory) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        answer = await client.call_tool(
            "fake__whoami", {"_farm": {"account": "fake-02", "timeout_s": 20}}, raise_on_error=False
        )
        assert answer.content[0].text == "fake-02"  # type: ignore[union-attr]  # pinned to that account
        await client.call_tool("fake__echo", {"message": "hi", "_farm": {"strategy": "most_remaining"}})

    seen = [call for server in servers.values() for call in server.state.calls]  # type: ignore[attr-defined]
    assert ("echo", {"message": "hi"}) in seen
    assert all("_farm" not in arguments for _, arguments in seen)


async def test_an_invalid_farm_argument_says_what_is_allowed(mcp_world: WorldFactory) -> None:
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": create_fake_rich_server()})
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        with pytest.raises(ToolError, match="allowed: account, strategy, timeout_s"):
            await client.call_tool("fake__echo", {"message": "x", "_farm": {"acount": "fake-01"}})


async def test_a_server_that_breaks_its_own_output_schema_is_still_relayed(mcp_world: WorldFactory) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway()

    # a client that does not police the server's contract either (a plain Client would raise: its own check)
    async with build_client(gateway, "test") as client:
        result = await client.call_tool_mcp("fake__schema_violation", {})

    assert result.is_error is False and result.structured_content == {"n": "x"}  # as the server said it
    runs = await fetch(world.ctx.pool, "select status from runs where capability = 'mcp:fake'")
    assert runs == [("succeeded",)]  # not a Farm failure: the relay does not police the server's contract


async def test_calls_alternate_between_accounts_with_round_robin(mcp_world: WorldFactory) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2, strategy="round_robin")), servers)
    await world.sync()
    gateway = await world.gateway()

    answered: list[str] = []
    async with Client(gateway) as client:
        for _ in range(4):
            answered.append((await client.call_tool("fake__whoami", {})).content[0].text)  # type: ignore[union-attr]

    assert answered[0] != answered[1] and answered[:2] == answered[2:]
    assert set(answered) == {"fake-01", "fake-02"}


async def test_a_transport_failure_moves_on_to_the_next_account_and_the_run_shows_both_attempts(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()
    await world.use("fake-01", broken_transport())  # the first account's server stops starting

    async with Client(gateway) as client:
        answer = await client.call_tool("fake__whoami", {})
    assert answer.content[0].text == "fake-02"  # type: ignore[union-attr]

    (run_id,) = [
        row[0] for row in await fetch(world.ctx.pool, "select id from runs where capability = 'mcp:fake'")
    ]
    trail = [(kind, conn) for kind, conn, _ in await events(world.ctx.pool, run_id)]
    assert [item for item in trail if item[0] in ("execute", "failure", "fallback", "success")] == [
        ("execute", "fake-01"),
        ("failure", "fake-01"),
        ("fallback", "fake-01"),
        ("execute", "fake-02"),
        ("success", "fake-02"),
    ]


async def test_a_failed_account_is_remembered_by_the_router_so_the_next_call_skips_it_first(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()
    await world.use("fake-01", broken_transport())

    async with Client(gateway) as client:
        for _ in range(2):
            await client.call_tool("fake__whoami", {})

    health = await fetch(
        world.ctx.pool,
        "select last_error_kind, failure_count from connection_health where connection_id = 'fake-01'",
    )
    assert health == [("server", 1)]  # tried once, failed once; the second call went straight to fake-02
    assert len(servers["fake-02"].state.calls) == 2  # type: ignore[attr-defined]


async def test_when_every_account_fails_the_error_names_each_account_and_the_kind(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()
    await world.use("fake-01", broken_transport())
    await world.use("fake-02", broken_transport())

    async with Client(gateway) as client:
        result = await client.call_tool_mcp("fake__echo", {"message": "x"})

    text = result.content[0].text  # type: ignore[union-attr]
    assert result.is_error is True
    assert "fake-01" in text and "fake-02" in text and "server" in text and "get_run" in text


async def test_identical_calls_both_run_and_their_arguments_are_never_stored(mcp_world: WorldFactory) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway()
    secret = "ARG-SENTINEL-5d1c9e"

    async with Client(gateway) as client:
        await asyncio.gather(
            client.call_tool("fake__echo", {"message": secret}),
            client.call_tool("fake__echo", {"message": secret}),
        )

    assert [call for call in remote.state.calls if call[0] == "echo"] == [("echo", {"message": secret})] * 2  # type: ignore[attr-defined]
    assert secret not in await database_text(world.ctx.pool)  # not in params, events, runs or anywhere else


async def test_the_router_serves_a_pass_through_capability_like_any_other(mcp_world: WorldFactory) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01")}
    world = await mcp_world(mcp_registry(fake=mcp_provider()), servers)
    await world.sync()
    await world.gateway()  # registers the mcp:fake capability

    outcome = await route(
        world.ctx,
        "mcp:fake",
        {"tool": "add", "arguments": {"a": 1, "b": 2}, "call": "direct-router-call"},
        caller="test",
    )

    assert outcome.ok and outcome.source is not None and outcome.source.connection_id == "fake-01"
    assert outcome.cost.units == {"calls": 1.0}  # the quota unit of a pass-through provider


# --- the account's own limits: quota, budget, cooldown, login ------------------------------------------------


async def test_the_quota_unit_calls_is_counted_and_an_exhausted_account_is_never_called(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    units = {"calls": {"limit": 1, "period": "day", "charged_on": "success"}}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2, units=units)), servers)
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        first = await client.call_tool("fake__whoami", {})
        second = await client.call_tool("fake__whoami", {})
        third = await client.call_tool_mcp("fake__whoami", {})

    assert {first.content[0].text, second.content[0].text} == {"fake-01", "fake-02"}  # type: ignore[union-attr]
    text = third.content[0].text  # type: ignore[union-attr]
    assert third.is_error and "no_capacity" in text and "not enough 'calls' left" in text
    assert sum(len(s.state.calls) for s in servers.values()) == 2  # type: ignore[attr-defined]  # never reached
    used = await fetch(
        world.ctx.pool, "select connection_id, used from quota_usage where unit = 'calls' order by 1"
    )
    assert [(c, float(u)) for c, u in used] == [("fake-01", 1.0), ("fake-02", 1.0)]


async def test_a_budget_of_zero_blocks_a_paid_account_but_the_error_says_so(mcp_world: WorldFactory) -> None:
    units = {"calls": {"limit": None, "unit_cost_usd": 0.05, "charged_on": "success"}}
    registry = mcp_registry(fake=mcp_provider(units=units), budgets={"per_provider": {"fake": 0}})
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(registry, {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        result = await client.call_tool_mcp("fake__echo", {"message": "x"})

    assert result.is_error and "policy_blocked" in result.content[0].text  # type: ignore[union-attr]
    assert remote.state.calls == []  # type: ignore[attr-defined]


async def test_the_caller_of_a_pass_through_call_is_recorded_like_any_other(mcp_world: WorldFactory) -> None:
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": create_fake_rich_server()})
    await world.sync()
    gateway = await world.gateway()

    async with Client(
        gateway, client_info=mcp_types.Implementation(name="claude-code", version="2")
    ) as client:
        await client.call_tool("fake__echo", {"message": "hi"})

    runs = await fetch(
        world.ctx.pool, "select caller, strategy, status from runs where capability = 'mcp:fake'"
    )
    assert runs == [("claude", None, "succeeded")]


async def test_a_server_that_rejects_the_request_itself_is_relayed_not_failed_over(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        result = await client.call_tool_mcp("fake__rejected", {})

    assert result.is_error and "the server rejected this request" in result.content[0].text  # type: ignore[union-attr]
    (run_id,) = [
        r[0] for r in await fetch(world.ctx.pool, "select id from runs where capability = 'mcp:fake'")
    ]
    trail = [kind for kind, _, _ in await events(world.ctx.pool, run_id)]
    assert trail.count("execute") == 1 and "failure" not in trail  # one attempt: the same answer everywhere


async def test_a_call_that_takes_longer_than_asked_fails_as_a_timeout_and_the_next_call_still_works(
    mcp_world: WorldFactory,
) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider()), {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        slow = await client.call_tool_mcp("fake__slow", {"seconds": 5, "_farm": {"timeout_s": 0.4}})
        after = await client.call_tool("fake__echo", {"message": "still here"})

    assert slow.is_error and "timeout" in slow.content[0].text  # type: ignore[union-attr]
    assert after.content[0].text == "echo: still here"  # type: ignore[union-attr]
    kinds = await fetch(world.ctx.pool, "select error_kind from runs where status = 'failed'")
    assert kinds == [("timeout",)]


async def test_a_rate_limited_account_cools_down_for_as_long_as_it_asked_and_the_next_one_answers(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()
    await world.use("fake-01", http_answering(429, {"retry-after": "120"}))

    async with Client(gateway) as client:
        answers = [(await client.call_tool("fake__whoami", {})).content[0].text for _ in range(2)]  # type: ignore[union-attr]

    assert answers == ["fake-02", "fake-02"]
    ((kind, cooldown_s),) = await fetch(
        world.ctx.pool,
        "select last_error_kind, extract(epoch from cooldown_until - now()) from connection_health "
        "where connection_id = 'fake-01'",
    )
    assert kind == "rate_limited" and 90 < float(cooldown_s) <= 125  # the server's Retry-After, not a guess
    assert len(servers["fake-01"].state.calls) == 0  # type: ignore[attr-defined]
    assert len(servers["fake-02"].state.calls) == 2  # type: ignore[attr-defined]


async def test_a_refused_login_flags_the_account_for_a_person_and_the_next_one_answers(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2)), servers)
    await world.sync()
    gateway = await world.gateway()
    await world.use("fake-01", http_answering(401))

    async with Client(gateway) as client:
        answer = await client.call_tool("fake__whoami", {})

    assert answer.content[0].text == "fake-02"  # type: ignore[union-attr]
    status = await fetch(world.ctx.pool, "select status from connections where id = 'fake-01'")
    assert status == [("needs_login",)]
    assert await fetch(world.ctx.pool, "select kind, ref from alerts") == [("needs_login", "login:fake-01")]


async def test_a_pass_through_provider_row_that_no_longer_validates_is_skipped_not_fatal(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    world = await mcp_world(
        mcp_registry(fake=mcp_provider()), {"fake-01": create_fake_rich_server("fake-01")}
    )
    await world.sync()
    async with pool.connection() as conn:  # a hand-edited row
        await conn.execute(
            "insert into providers (id, name, kind, executor, config) values "
            "('broken', 'Broken', 'tool', 'mcp', %s)",
            (Jsonb({"mcp": {"transport": "carrier-pigeon"}}),),
        )

    gateway = await world.gateway()

    async with Client(gateway) as client:
        assert (await client.call_tool("fake__whoami", {})).content[0].text == "fake-01"  # type: ignore[union-attr]


# --- real transports ---------------------------------------------------------------------------------------------


STDIO_SERVER = Path(__file__).parent / "fixtures" / "mcp" / "stdio_server.py"


@pytest_asyncio.fixture
async def real_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path, real_transports=True) as make:
        yield make


def stdio_children() -> list[psutil.Process]:
    return [
        p for p in psutil.Process().children(recursive=True) if "stdio_server.py" in " ".join(p.cmdline())
    ]


async def test_a_real_stdio_server_gets_only_its_configured_environment_and_is_stopped_on_close(
    real_world: WorldFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FARM_MCP_PROBE_TEST_VALUE", "resolved-probe-value-42")
    monkeypatch.setenv(
        "FARM_UNRELATED_PROVIDER_KEY", "must-not-reach-the-child-7"
    )  # a key of the Farm itself
    provider = mcp_provider(
        command=sys.executable, args=[str(STDIO_SERVER)], env={"PROBE": "env:FARM_MCP_PROBE_TEST_VALUE"}
    )
    world = await real_world(mcp_registry(live=provider), {})
    (synced,) = await world.sync()
    assert synced.ok and set(synced.tools) == {"echo", "env_probe"}
    gateway = await world.gateway()

    async with Client(gateway) as client:
        echoed = await client.call_tool("live__echo", {"message": "hi"})
        configured = await client.call_tool("live__env_probe", {"name": "PROBE"})
        outsider = await client.call_tool("live__env_probe", {"name": "FARM_UNRELATED_PROVIDER_KEY"})

    assert echoed.content[0].text == "stdio echo: hi"  # type: ignore[union-attr]
    assert configured.content[0].text == "resolved-probe-value-42"  # type: ignore[union-attr]
    assert outsider.content[0].text == "<unset>"  # type: ignore[union-attr]  # the Farm's environment stays home
    assert stdio_children()  # kept running between calls...

    await world.executor.aclose()
    for _ in range(50):
        if not stdio_children():
            break
        await asyncio.sleep(0.1)
    assert not stdio_children()  # ...and gone when the executor is closed


async def test_a_missing_secret_is_a_login_failure_that_names_the_variable(real_world: WorldFactory) -> None:
    provider = mcp_provider(
        command=sys.executable, args=[str(STDIO_SERVER)], env={"PROBE": "env:FARM_MCP_NOT_SET_ANYWHERE"}
    )
    world = await real_world(mcp_registry(live=provider), {})

    (result,) = await world.sync()

    assert not result.ok and result.error is not None
    assert "FARM_MCP_NOT_SET_ANYWHERE" in result.error and "farm set-secret" in result.error


# --- building transports from configuration ------------------------------------------------------------------


def view(**fields: Any) -> ConnectionView:
    return ConnectionView(**{"id": "c-01", "provider_id": "p", "auth_ref": "cli:none", **fields})


def make_spec(**fields: Any) -> McpProviderSpec:
    return McpProviderSpec.model_validate(fields)


def no_tokens() -> Any:
    raise AssertionError("no token store expected")


def test_a_connections_own_env_replaces_the_providers_and_only_references_are_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARM_MCP_T_ONE", "value-one")
    monkeypatch.setenv("FARM_MCP_T_TWO", "value-two")
    monkeypatch.setenv("FARM_MCP_T_THREE", "value-three")
    spec = make_spec(command="srv", args=["--x"], env={"A": "env:FARM_MCP_T_ONE", "B": "env:FARM_MCP_T_TWO"})
    own = view(meta={"mcp": {"env": {"B": "env:FARM_MCP_T_THREE"}}})

    transport = build_transport(own, spec, token_storage=no_tokens)

    assert isinstance(transport, StdioTransport)
    assert (transport.command, transport.args) == ("srv", ["--x"])
    assert transport.env == {"A": "value-one", "B": "value-three"}  # nothing else: not the Farm's environment


def test_a_remote_server_gets_its_headers_and_the_accounts_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FARM_MCP_T_TEAM", "team-7")
    monkeypatch.setenv("FARM_MCP_T_BEARER", "bearer-of-account-1")
    spec = make_spec(
        transport="http",
        url="https://mcp.example.com/mcp",
        auth="env",
        headers={"X-Team": "env:FARM_MCP_T_TEAM"},
    )

    transport = build_transport(view(auth_ref="env:FARM_MCP_T_BEARER"), spec, token_storage=no_tokens)

    assert isinstance(transport, StreamableHttpTransport)
    assert transport.headers == {"X-Team": "team-7"}
    assert transport.auth is not None and transport.auth.token.get_secret_value() == "bearer-of-account-1"  # type: ignore[attr-defined]


def test_a_serving_farm_never_opens_a_browser_for_oauth_but_a_login_may(tmp_path: Path) -> None:
    spec = make_spec(transport="http", url="https://mcp.example.com/mcp", auth="oauth")

    def store() -> FileTreeStore:
        return FileTreeStore(data_directory=str(tmp_path))

    serving = build_transport(view(auth_ref="token-store:c-01"), spec, token_storage=store)
    login = build_transport(view(auth_ref="token-store:c-01"), spec, token_storage=store, interactive=True)

    assert isinstance(serving, StreamableHttpTransport) and isinstance(serving.auth, HeadlessOAuth)
    assert isinstance(login, StreamableHttpTransport) and not isinstance(login.auth, HeadlessOAuth)
    with pytest.raises(LoginRequired, match="farm mcp login"):
        asyncio.run(serving.auth.redirect_handler("https://auth.example.com/authorize"))


def test_a_url_that_is_a_secret_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_MCP_T_URL", "https://hooks.example.com/mcp/s/abc123")
    spec = make_spec(transport="sse", url="env:FARM_MCP_T_URL")

    transport = build_transport(view(), spec, token_storage=no_tokens)

    assert isinstance(transport, SSETransport) and transport.url == "https://hooks.example.com/mcp/s/abc123"


def test_a_variable_that_is_not_set_is_reported_by_name_not_value() -> None:
    spec = make_spec(command="srv", env={"A": "env:FARM_MCP_NEVER_SET_X"})

    with pytest.raises(McpFailure) as caught:
        build_transport(view(), spec, token_storage=no_tokens)

    assert caught.value.kind is ErrorKind.NEEDS_LOGIN and "FARM_MCP_NEVER_SET_X" in caught.value.message


# --- how a path failure is classified -------------------------------------------------------------------------


def http_answering(status: int, headers: dict[str, str] | None = None) -> StreamableHttpTransport:
    """A real streamable-http transport whose server answers everything with ``status`` (no network)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, headers=headers or {}, content=b"no", request=request)

    def factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handler), **kwargs)

    return StreamableHttpTransport("http://127.0.0.1:9/mcp", httpx_client_factory=factory)


@pytest.mark.parametrize(
    ("status", "headers", "kind", "retry_after"),
    [
        (401, {}, "auth", None),
        (403, {}, "auth", None),
        (402, {}, "limit_reached", None),
        (429, {"retry-after": "7"}, "rate_limited", 7.0),
        (500, {}, "server", None),
        (503, {}, "server", None),
        (404, {}, "server", None),
        (400, {}, "bad_request", None),
    ],
)
async def test_an_http_error_answer_is_classified_by_its_status(
    status: int, headers: dict[str, str], kind: str, retry_after: float | None
) -> None:
    client = build_client(http_answering(status, headers), "x-01")

    with pytest.raises(Exception) as caught:  # noqa: PT011  (what the SDK raises is the thing under test)
        await relay.list_tools(client, timeout_s=15)

    failure = relay.classify(caught.value, http=client.transport.http_status.take())
    assert (failure.kind.value, failure.retry_after_s) == (kind, retry_after)
    assert "HTTP" in failure.message


async def test_a_server_that_is_not_listening_and_a_login_that_is_needed_are_classified() -> None:
    refused = build_client(StreamableHttpTransport("http://127.0.0.1:9/mcp"), "x-01")

    with pytest.raises(Exception) as caught:  # noqa: PT011
        await relay.list_tools(refused, timeout_s=15)

    assert relay.classify(caught.value).kind is ErrorKind.SERVER
    assert relay.classify(LoginRequired("log in"), oauth=True).kind is ErrorKind.NEEDS_LOGIN
    assert relay.classify(TimeoutError()).kind is ErrorKind.TIMEOUT


def test_only_a_json_rpc_error_of_the_servers_own_is_a_rejection_not_the_sdks_stand_in() -> None:
    own = MCPError(mcp_types.INVALID_PARAMS, "unknown tool 'x'")
    stand_in = MCPError(mcp_types.METHOD_NOT_FOUND, "Not Found")  # what the SDK says for an HTTP 404
    internal = MCPError(mcp_types.INTERNAL_ERROR, "the server crashed")

    assert relay.rejecting_error(own) is own
    assert relay.rejecting_error(stand_in) is None and relay.rejecting_error(internal) is None
