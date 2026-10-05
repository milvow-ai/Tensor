"""Helpers for the OPEN1 tests: registries of MCP providers, in-process servers behind connections, a gateway.

``world_factory`` is the whole chain with only the servers faked: a registry parsed, validated and synced into
Postgres like production, the real ``McpExecutor`` (database directory, real relay client, real error
classification), the real router and the real gateway. Each connection reaches the in-process FastMCP server
(or the real, failing transport) the test puts behind its id.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mcp_types
from fastmcp import FastMCP
from fastmcp.client.transports import ClientTransport, StdioTransport
from psycopg import sql

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ConnectionView
from farm.executors.mcp.client import McpExecutor
from farm.gateway.server import build_server
from farm.mcp.store import DbDirectory
from farm.mcp.sync import SyncResult, sync_all
from farm.registry import Registry, parse_registry
from farm.registry.models import McpProviderSpec
from farm.registry.sync import sync_registry
from tests.conftest import truncate_all

type Target = FastMCP | ClientTransport


def mcp_provider(
    *,
    accounts: int = 1,
    expose: str = "direct",
    strategy: str = "failover",
    namespace: str | None = None,
    allow: Sequence[str] = ("*",),
    deny: Sequence[str] = (),
    auth_ref: str = "cli:none",
    units: Mapping[str, Any] | None = None,
    **block: Any,
) -> dict[str, Any]:
    """One ``executor: mcp`` provider as registry data; its connections are ``<provider>-01``, ``-02``, ..."""
    mcp: dict[str, Any] = {
        "command": "python",
        "args": ["a-server-the-tests-replace.py"],
        "expose": expose,
        "tools": {"allow": list(allow), "deny": list(deny)},
        **block,
    }
    if namespace is not None:
        mcp["namespace"] = namespace
    return {
        "kind": "tool",
        "executor": "mcp",
        "default_strategy": strategy,
        "mcp": mcp,
        "_accounts": accounts,
        "_auth_ref": auth_ref,
        "_units": dict(units) if units else None,
    }


def mcp_registry(*, budgets: Mapping[str, Any] | None = None, **providers: dict[str, Any]) -> Registry:
    """A valid registry of the given MCP providers (see :func:`mcp_provider`) and nothing else."""
    document: dict[str, Any] = {"providers": {}, "capabilities": {}}
    if budgets is not None:
        document["budgets"] = dict(budgets)
    for provider_id, definition in providers.items():
        entry = {k: v for k, v in definition.items() if not k.startswith("_")}
        entry["connections"] = [
            {
                "id": f"{provider_id}-{n:02d}",
                "auth_ref": definition["_auth_ref"],
                "priority": n,
                **({"units": definition["_units"]} if definition["_units"] else {}),
            }
            for n in range(1, definition["_accounts"] + 1)
        ]
        document["providers"][provider_id] = entry
    return parse_registry(
        json.dumps({"settings": {"owner_email": "owner@example.com"}, **document}), source="<test>"
    )


def broken_transport() -> ClientTransport:
    """A real transport that fails to start: the program does not exist."""
    return StdioTransport(command="open1-this-program-does-not-exist", args=[])


@dataclass
class McpWorld:
    ctx: FarmContext
    executor: McpExecutor
    servers: dict[str, Target]
    """Connection id -> what its transport reaches. Change it with ``await world.use(...)``."""

    async def use(self, connection_id: str, target: Target) -> None:
        """Put another server (or a broken transport) behind a connection, as if it had been restarted."""
        self.servers[connection_id] = target
        await self.executor.discard(connection_id)

    async def sync(self, provider: str | None = None) -> list[SyncResult]:
        return await sync_all(self.ctx.pool, self.executor, only=provider, timeout_s=20)

    async def gateway(self, **options: Any) -> FastMCP:
        return await build_server(self.ctx, **options)


type WorldFactory = Callable[[Registry, Mapping[str, Target]], Awaitable[McpWorld]]


@contextlib.asynccontextmanager
async def world_factory(
    pool: DbPool, tmp_path: Path, *, real_transports: bool = False
) -> AsyncIterator[WorldFactory]:
    """``await make(registry, {connection id: server})``: the registry is synced into the test database.

    ``real_transports``: the executor builds its transports from the registry like production (no servers are
    handed in; start real subprocesses).

    Test modules wrap it in a fixture named ``mcp_world`` (``async with world_factory(pool, tmp_path) as make:
    yield make``); everything is torn down and the tables are emptied again afterwards.
    """
    worlds: list[McpWorld] = []

    async def make(registry: Registry, servers: Mapping[str, Target]) -> McpWorld:
        await sync_registry(pool, registry)
        targets = dict(servers)

        def transport_of(connection: ConnectionView, spec: McpProviderSpec) -> Target:
            return targets[connection.id]

        executor = McpExecutor(
            data_dir=tmp_path,
            directory=DbDirectory(pool),
            transports=None if real_transports else transport_of,
        )
        ctx = FarmContext(pool=pool, executors={"mcp": executor}, poll_interval_s=0.02)
        world = McpWorld(ctx, executor, targets)
        worlds.append(world)
        return world

    yield make
    for world in worlds:
        await world.ctx.aclose()
    await truncate_all(pool)


def comparable(result: mcp_types.CallToolResult) -> dict[str, Any]:
    """A ``CallToolResult`` as JSON, without the ``_meta`` entries that name the server that sent it."""
    dumped: dict[str, Any] = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    meta = dumped.get("_meta")
    if meta is not None:
        meta.pop("io.modelcontextprotocol/serverInfo", None)
        if not meta:
            del dumped["_meta"]
    return dumped


async def database_text(pool: DbPool) -> str:
    """Every text / json value in every table of the public schema, as one string (to prove what is NOT in it)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select table_name, column_name from information_schema.columns where table_schema = 'public' "
            "and data_type in ('text', 'json', 'jsonb', 'character varying')"
        )
        columns = await cur.fetchall()
        chunks: list[str] = []
        for table, column in columns:
            query = sql.SQL("select {}::text from public.{}").format(
                sql.Identifier(column), sql.Identifier(table)
            )
            rows = await (await conn.execute(query)).fetchall()
            chunks.extend(str(row[0]) for row in rows if row[0] is not None)
    return "\n".join(chunks)
