"""OPEN1 acceptance: any MCP server through the Farm, as is. One test per line of the brief's list.

No network. The servers are the fake MCP servers of ``tests/fake_mcp_server.py`` (image block,
``structuredContent``, an ``isError`` tool, a protocol-level rejection) or real stdio subprocesses; everything
between them and the client is the real code: registry, Postgres, executor, router, gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import mcp_types
import pytest
import pytest_asyncio
from alembic import command
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from structlog.testing import capture_logs
from typer.testing import CliRunner

from farm.control.cli import alembic_config, app
from farm.db.pool import DbPool
from farm.mcp.importer import format_report, import_servers
from farm.registry import load_registry
from farm.settings import load_env
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

FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
STDIO_SERVER = FIXTURES / "stdio_server.py"
REAL_REGISTRY = Path(__file__).parent.parent / "config" / "registry.example.yaml"
FARM_DIR = Path(__file__).parent.parent / "farm"


@pytest_asyncio.fixture
async def mcp_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path) as make:
        yield make


@pytest_asyncio.fixture
async def real_world(pool: DbPool, tmp_path: Path) -> AsyncIterator[WorldFactory]:
    async with world_factory(pool, tmp_path, real_transports=True) as make:
        yield make


# --- import: all three formats, secrets only in .env ----------------------------------------------------------


def test_accept_open1_import_from_all_three_formats_keeps_every_secret_in_env_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    registry = tmp_path / "registry.yaml"
    shutil.copy(REAL_REGISTRY, registry)
    env = tmp_path / ".env"
    sources = {
        "claude-desktop": FIXTURES / "claude_desktop_config.json",
        "claude-code": FIXTURES / "claude_code.json",
        "codex": FIXTURES / "codex_config.toml",
    }
    sentinels = re.findall(
        r"SENTINEL-[a-z0-9-]+", "".join(p.read_text(encoding="utf-8") for p in sources.values())
    )
    assert len(sentinels) >= 7

    with capture_logs() as structured, caplog.at_level(logging.DEBUG):
        dry = [
            format_report(import_servers(f"file:{p}", registry_path=registry, env_path=env, dry_run=True))
            for p in sources.values()
        ]
        assert not env.exists()  # a dry run writes nothing
        for p in sources.values():
            import_servers(f"file:{p}", registry_path=registry, env_path=env)

    # providers were created, and the registry that holds them loads
    providers = load_registry(registry).providers
    assert {"filesystem", "github", "linear", "api", "context7", "figma"} <= set(providers)
    # a literal token ended up in .env as a reference target...
    env_text = env.read_text(encoding="utf-8")
    assert "FARM_MCP_GITHUB_GITHUB_PERSONAL_ACCESS_TOKEN=SENTINEL-desktop-github-token-7f3a" in env_text
    assert (
        providers["github"].mcp.env["GITHUB_PERSONAL_ACCESS_TOKEN"]
        == "env:FARM_MCP_GITHUB_GITHUB_PERSONAL_ACCESS_TOKEN"
    )  # type: ignore[union-attr]
    # ...and the token string appears nowhere in the registry, the output or the logs; a dry run shows no values
    elsewhere = registry.read_text(encoding="utf-8") + caplog.text + json.dumps(structured)
    assert not [s for s in sentinels if s in elsewhere]
    assert not [
        s for s in sentinels if s in "\n".join(dry)
    ]  # the dry-run output names variables, never values


async def test_accept_open1_a_secret_reaches_the_server_but_never_a_row_a_log_or_the_registry(
    real_world: WorldFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    pool: DbPool,
) -> None:
    secret = "SENTINEL-e2e-probe-secret-61c4"
    source = tmp_path / "claude_desktop_config.json"
    source.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "probe": {
                        "command": sys.executable,
                        "args": [str(STDIO_SERVER)],
                        "env": {"PROBE": secret},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    registry_file, env_file = tmp_path / "registry.yaml", tmp_path / ".env"
    registry_file.write_text(
        "settings: {owner_email: me@example.com}\nproviders: {}\ncapabilities: {}\n", encoding="utf-8"
    )
    import_servers(f"file:{source}", registry_path=registry_file, env_path=env_file)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    load_env(env_file)  # what `farm serve` does at start-up: the secret enters the process environment

    world = await real_world(load_registry(registry_file), {})
    with capture_logs() as structured, caplog.at_level(logging.DEBUG):
        (synced,) = await world.sync()
        gateway = await world.gateway()
        async with Client(gateway) as client:
            answer = await client.call_tool("probe__env_probe", {"name": "PROBE"})

    assert synced.ok and answer.content[0].text == secret  # type: ignore[union-attr]  # the server did receive it
    assert secret not in await database_text(
        pool
    )  # no run, event, request, provider or connection row has it
    assert secret not in registry_file.read_text(encoding="utf-8") + caplog.text + json.dumps(structured)


# --- sync ---------------------------------------------------------------------------------------------------------


async def test_accept_open1_sync_stores_tools_a_deny_list_hides_one_and_a_changed_schema_bumps_the_hash(
    mcp_world: WorldFactory, pool: DbPool
) -> None:
    first = FastMCP("fake-01")

    @first.tool(name="echo")
    def echo(message: str) -> str:
        """Echo the message back."""
        return message

    @first.tool(name="secret_admin")
    def secret_admin() -> str:
        """Not for the AI."""
        return "no"

    world = await mcp_world(mcp_registry(fake=mcp_provider(deny=["secret_*"])), {"fake-01": first})

    (result,) = await world.sync()
    before = dict(await fetch(pool, "select name, schema_hash from mcp_tools where provider = 'fake'"))

    assert result.ok and set(before) == {"echo"}  # stored; the deny-listed tool is hidden
    second = FastMCP("fake-01")

    @second.tool(name="echo")
    def echo_changed(message: str, shout: bool = False) -> str:
        """Echo the message back, optionally louder."""
        return message

    await world.use("fake-01", second)
    await world.sync()
    after = dict(await fetch(pool, "select name, schema_hash from mcp_tools where provider = 'fake'"))
    assert after["echo"] != before["echo"]  # a changed schema is a new hash


# --- direct mode ---------------------------------------------------------------------------------------------------


async def test_accept_open1_direct_mode_lists_the_servers_definition_byte_for_byte_and_returns_identical_blocks(
    mcp_world: WorldFactory,
) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider(expose="direct")), {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client, Client(remote) as direct:
        listed = {t.name: t for t in await client.list_tools()}
        original = {t.name: t for t in await direct.list_tools()}
        # fake__echo: byte-identical description and input schema
        assert listed["fake__echo"].description == original["echo"].description
        assert json.dumps(listed["fake__echo"].input_schema, sort_keys=True) == json.dumps(
            original["echo"].input_schema, sort_keys=True
        )
        # calling it returns content blocks identical to calling the fake server directly (compare JSON)
        for tool, arguments in (("echo", {"message": "hello"}), ("rich_echo", {"message": "pic"})):
            via_farm = await client.call_tool_mcp(f"fake__{tool}", arguments)
            as_is = await direct.call_tool_mcp(tool, arguments)
            assert comparable(via_farm) == comparable(as_is)

        rich = await client.call_tool_mcp("fake__rich_echo", {"message": "pic"})

    assert any(
        isinstance(block, mcp_types.ImageContent) and block.data == FAKE_PNG_B64 for block in rich.content
    )
    assert rich.structured_content == {
        "message": "pic",
        "blocks": 5,
    }  # structuredContent, as the server sent it


# --- discovery mode --------------------------------------------------------------------------------------------------


async def test_accept_open1_discovery_mode_finds_a_tool_with_its_schema_and_calls_it_with_an_identical_result(
    mcp_world: WorldFactory,
) -> None:
    remote = create_fake_rich_server("fake-01")
    world = await mcp_world(mcp_registry(fake=mcp_provider(expose="discovery")), {"fake-01": remote})
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client, Client(remote) as direct:
        listed = {t.name for t in await client.list_tools()}
        assert (
            "fake__echo" not in listed and {"search_tools", "call_tool"} <= listed
        )  # nothing listed directly

        found = await client.call_tool("search_tools", {"query": "echo"})
        matches = [t for t in found.structured_content["result"] if t["name"] == "fake__echo"]  # type: ignore[index]
        original = {t.name: t for t in await direct.list_tools()}["echo"]
        assert (
            matches and matches[0]["inputSchema"] == original.input_schema
        )  # the schema comes with the match

        via_farm = await client.call_tool_mcp(
            "call_tool", {"name": "fake__rich_echo", "arguments": {"message": "x"}}
        )
        as_is = await direct.call_tool_mcp("rich_echo", {"message": "x"})

    assert comparable(via_farm) == comparable(as_is)


# --- two connections: round robin, failover, remote isError ------------------------------------------------------------


async def test_accept_open1_two_connections_alternate_fail_over_on_transport_errors_and_return_iserror_as_is(
    mcp_world: WorldFactory,
) -> None:
    servers = {"fake-01": create_fake_rich_server("fake-01"), "fake-02": create_fake_rich_server("fake-02")}
    world = await mcp_world(mcp_registry(fake=mcp_provider(accounts=2, strategy="round_robin")), servers)
    await world.sync()
    gateway = await world.gateway()

    async with Client(gateway) as client:
        # round_robin: the calls alternate between the two connections
        served = [(await client.call_tool("fake__whoami", {})).content[0].text for _ in range(4)]  # type: ignore[union-attr]
        assert served[0] != served[1] and served[:2] == served[2:]

        # a remote isError result comes back as it is, with no failover
        soft = await client.call_tool_mcp("fake__soft_error", {"detail": "bad input"})
        assert soft.is_error and soft.structured_content == {"detail": "bad input"}

        # one connection fails at the transport: the call succeeds on the other
        await world.use("fake-01", broken_transport())
        survivors = [(await client.call_tool("fake__whoami", {})).content[0].text for _ in range(3)]  # type: ignore[union-attr]
    assert survivors == ["fake-02"] * 3

    runs = await fetch(
        world.ctx.pool, "select id from runs where capability = 'mcp:fake' order by started_at"
    )
    soft_trail = [k for k, _, _ in await events(world.ctx.pool, runs[4][0])]
    assert (
        soft_trail.count("execute") == 1 and "failure" not in soft_trail
    )  # isError: one attempt, no failover
    failing = [(k, c) for rid in (r[0] for r in runs[5:]) for k, c, _ in await events(world.ctx.pool, rid)]
    # the first call after the break shows both attempts (a failure on fake-01, the answer on fake-02)
    assert ("failure", "fake-01") in failing and ("success", "fake-02") in failing


# --- farm serve with a provider that cannot start ----------------------------------------------------------------------


def _registry_text(dead_command: str) -> str:
    def provider(command: str) -> str:
        return (
            "    kind: tool\n    executor: mcp\n    mcp:\n"
            f"      command: {json.dumps(command)}\n      args: [{json.dumps(str(STDIO_SERVER))}]\n"
            "      expose: direct\n"
        )

    return (
        "settings: {owner_email: owner@example.com}\ncapabilities: {}\nproviders:\n"
        f"  live:\n{provider(sys.executable)}    connections: [{{id: live-01, auth_ref: 'cli:none'}}]\n"
        f"  dead:\n{provider(dead_command)}    connections: [{{id: dead-01, auth_ref: 'cli:none'}}]\n"
    )


async def _talk_to_farm_serve(url: str) -> dict[str, Any]:
    transport = StdioTransport(
        command=sys.executable,
        args=["-c", "from farm.control.cli import app; app()", "serve"],
        env={**os.environ, "FARM_DB_URL": url, "FARM_LOG_LEVEL": "INFO"},
    )
    async with Client(transport) as client:
        listed = sorted(t.name for t in await client.list_tools())
        live = await client.call_tool("live__echo", {"message": "hi"})
        dead = await client.call_tool_mcp("dead__echo", {"message": "hi"})
        again = await client.call_tool("live__echo", {"message": "still fine"})
    return {
        "listed": listed,
        "live": live.content[0].text,  # type: ignore[union-attr]
        "dead": {"is_error": dead.is_error, "text": dead.content[0].text},  # type: ignore[union-attr]
        "again": again.content[0].text,  # type: ignore[union-attr]
    }


def test_accept_open1_a_provider_whose_server_cannot_start_does_not_break_farm_serve_or_the_others(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    registry = tmp_path / "registry.yaml"
    runner = CliRunner()

    registry.write_text(_registry_text(sys.executable), encoding="utf-8")  # both servers work at first
    assert runner.invoke(app, ["registry", "sync", str(registry)]).exit_code == 0
    first = runner.invoke(app, ["mcp", "sync"])
    assert first.exit_code == 0, first.output
    assert "live: 2 tools via live-01" in first.output and "dead: 2 tools via dead-01" in first.output

    registry.write_text(_registry_text("open1-no-such-program"), encoding="utf-8")  # then one cannot start
    assert runner.invoke(app, ["registry", "sync", str(registry)]).exit_code == 0
    second = runner.invoke(app, ["mcp", "sync"])
    assert second.exit_code == 1  # reported, not raised
    assert "dead: FAILED" in second.output and "dead-01: server" in second.output
    assert "live: 2 tools via live-01" in second.output  # the others are unaffected

    seen = asyncio.run(asyncio.wait_for(_talk_to_farm_serve(url), 180))

    assert {"live__echo", "live__env_probe", "dead__echo", "dead__env_probe"} <= set(
        seen["listed"]
    )  # still served
    assert seen["live"] == "stdio echo: hi" and seen["again"] == "stdio echo: still fine"
    assert seen["dead"]["is_error"] is True
    assert (
        "dead-01" in seen["dead"]["text"] and "server" in seen["dead"]["text"]
    )  # names the account and the kind


# --- no test-name checks in the product -------------------------------------------------------------------------------


def test_accept_open1_no_test_name_checks_remain_in_farm() -> None:
    offenders = [
        f"{path.relative_to(FARM_DIR.parent)}:{number}"
        for path in FARM_DIR.rglob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"PYTEST_CURRENT_TEST|legacy_patterns|is_legacy_|_legacy_m1_surface|[\"']test_accept_|[\"']test_gateway\.py::", line)
    ]

    assert offenders == []
