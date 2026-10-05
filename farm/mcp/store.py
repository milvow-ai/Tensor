"""What the Farm keeps about its MCP servers in Postgres: the pass-through providers and their tools.

* ``providers.config.mcp`` (written by ``farm registry sync``) says how to reach a server and how to present
  it (:class:`~farm.registry.models.McpProviderSpec`). A row that no longer validates is skipped with a log
  line: one bad provider must not stop the gateway or the others.
* ``mcp_tools`` (written by ``farm mcp sync``) is the catalogue the gateway publishes. A tool is stored
  whole (``definition``) next to the columns a query wants, with the hash that makes a change visible.
* ``capabilities`` / ``capability_routes`` get one ``mcp:<provider>`` entry per provider: that capability is
  how a pass-through call reaches the router (pool, strategy, health, quota, run history).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import mcp_types
import structlog
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from farm.db.pool import DbPool
from farm.executors.base import ConnectionView
from farm.registry.models import MCP_CAPABILITY_PREFIX, McpProviderSpec

log = structlog.get_logger(__name__)


class DbDirectory:
    """``McpDirectory`` over ``providers.config``: the executor finds a provider's ``mcp`` block here."""

    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def mcp_spec(self, provider_id: str) -> McpProviderSpec | None:
        providers = await list_providers(self._pool, provider_id=provider_id, enabled_only=False)
        return providers[0].spec if providers else None


@dataclass(frozen=True)
class Account:
    """One connection of a pass-through provider and whether it may be used right now."""

    view: ConnectionView
    status: str
    circuit: str
    cooldown_until: datetime | None


@dataclass(frozen=True)
class McpProvider:
    """A provider that fronts an MCP server (``executor: mcp`` with an ``mcp:`` block)."""

    id: str
    name: str
    enabled: bool
    spec: McpProviderSpec

    @property
    def namespace(self) -> str:
        return self.spec.namespace or self.id

    @property
    def capability(self) -> str:
        return f"{MCP_CAPABILITY_PREFIX}{self.id}"


@dataclass(frozen=True)
class StoredTool:
    provider: str
    definition: mcp_types.Tool
    schema_hash: str
    synced_at: datetime

    @property
    def name(self) -> str:
        return self.definition.name


@dataclass(frozen=True)
class SyncDiff:
    """What a sync changed in a provider's catalogue (names; ``changed`` also carries the two hashes)."""

    added: tuple[str, ...] = ()
    changed: tuple[tuple[str, str, str], ...] = ()
    """``(tool, old hash, new hash)``"""
    removed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()


def dump_tool(tool: mcp_types.Tool) -> dict[str, Any]:
    """The tool as it travels on the wire (aliases, no nulls): what is stored and what is hashed."""
    dumped: dict[str, Any] = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


def tool_hash(tool: mcp_types.Tool) -> str:
    """sha256 of the whole definition: any change to what an AI is shown (not only the schema) changes it."""
    canonical = json.dumps(dump_tool(tool), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def list_providers(
    pool: DbPool, *, provider_id: str | None = None, enabled_only: bool = True
) -> list[McpProvider]:
    """The MCP pass-through providers, by id. ``provider_id`` narrows to one; invalid rows are skipped."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select id, name, enabled, config -> 'mcp' from public.providers "
            "where executor = 'mcp' and config -> 'mcp' is not null "
            "and (%s::text is null or id = %s) and (enabled or not %s) order by id",
            (provider_id, provider_id, enabled_only),
        )
        rows = await cur.fetchall()
    providers: list[McpProvider] = []
    for row_id, name, enabled, block in rows:
        try:
            spec = McpProviderSpec.model_validate(block)
        except ValidationError as exc:
            log.error("mcp.provider_config_invalid", provider=row_id, problems=exc.error_count())
            continue
        providers.append(McpProvider(id=row_id, name=name, enabled=enabled, spec=spec))
    return providers


async def ensure_capability(pool: DbPool, provider: McpProvider) -> None:
    """Create ``mcp:<provider>`` and its route if they are missing (never touches an existing route: the
    owner may have switched it off). The capability has no default strategy of its own, so the provider's
    ``default_strategy`` decides how its accounts are used."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "insert into public.capabilities (name, kind, description, default_strategy, cache_ttl_seconds) "
            "values (%s, 'tool', %s, null, 0) on conflict (name) do nothing",
            (provider.capability, f"Pass-through to the MCP server of {provider.name}"),
        )
        await conn.execute(
            "insert into public.capability_routes (capability, provider_id, position, enabled) "
            "values (%s, %s, 0, true) on conflict (capability, provider_id) do nothing",
            (provider.capability, provider.id),
        )


async def load_tools(pool: DbPool, provider_ids: Sequence[str] | None = None) -> list[StoredTool]:
    """The stored catalogue (all providers, or the named ones), by provider and tool name."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select provider, definition, schema_hash, synced_at from public.mcp_tools "
            "where (%s::text[] is null or provider = any(%s)) order by provider, name",
            (None if provider_ids is None else list(provider_ids),) * 2,
        )
        rows = await cur.fetchall()
    tools: list[StoredTool] = []
    for provider, definition, schema_hash, synced_at in rows:
        try:
            definition_tool = mcp_types.Tool.model_validate(definition)
            tools.append(StoredTool(provider, definition_tool, schema_hash, synced_at))
        except ValidationError:
            log.error("mcp.stored_tool_invalid", provider=provider)
    return tools


async def replace_tools(
    pool: DbPool, provider_id: str, tools: Sequence[mcp_types.Tool], now: datetime
) -> SyncDiff:
    """Make the catalogue of ``provider_id`` equal ``tools`` in one transaction and say what changed."""
    wanted = {tool.name: tool for tool in tools}
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "select name, schema_hash from public.mcp_tools where provider = %s for update", (provider_id,)
        )
        known = {name: stored_hash for name, stored_hash in await cur.fetchall()}
        added: list[str] = []
        changed: list[tuple[str, str, str]] = []
        unchanged: list[str] = []
        for name, tool in wanted.items():
            new_hash = tool_hash(tool)
            old_hash = known.get(name)
            if old_hash == new_hash:
                unchanged.append(name)
                continue
            (added.append(name) if old_hash is None else changed.append((name, old_hash, new_hash)))
            definition = dump_tool(tool)
            await conn.execute(
                "insert into public.mcp_tools (provider, name, description, input_schema, output_schema, "
                "annotations, definition, schema_hash, synced_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (workspace_id, provider, name) do update set "
                "description = excluded.description, "
                "input_schema = excluded.input_schema, output_schema = excluded.output_schema, "
                "annotations = excluded.annotations, definition = excluded.definition, "
                "schema_hash = excluded.schema_hash, synced_at = excluded.synced_at",
                (
                    provider_id,
                    name,
                    tool.description or "",
                    Jsonb(definition["inputSchema"]),
                    Jsonb(definition["outputSchema"]) if "outputSchema" in definition else None,
                    Jsonb(definition["annotations"]) if "annotations" in definition else None,
                    Jsonb(definition),
                    new_hash,
                    now,
                ),
            )
        if unchanged:
            await conn.execute(
                "update public.mcp_tools set synced_at = %s where provider = %s and name = any(%s)",
                (now, provider_id, unchanged),
            )
        removed = sorted(set(known) - set(wanted))
        if removed:
            await conn.execute(
                "delete from public.mcp_tools where provider = %s and name = any(%s)", (provider_id, removed)
            )
    return SyncDiff(tuple(added), tuple(changed), tuple(removed), tuple(unchanged))


async def list_accounts(pool: DbPool, provider_id: str) -> list[Account]:
    """The provider's connections in the order the router would try them (priority, then id)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select cn.id, cn.provider_id, cn.auth_ref, cn.meta, cn.concurrency, cn.rate_per_min, cn.status, "
            "coalesce(h.circuit, 'closed'), h.cooldown_until "
            "from public.connections cn left join public.connection_health h on h.connection_id = cn.id "
            "where cn.provider_id = %s order by cn.priority, cn.id",
            (provider_id,),
        )
        rows = await cur.fetchall()
    return [
        Account(
            ConnectionView(
                id=row[0],
                provider_id=row[1],
                auth_ref=row[2] or "",
                meta=row[3] or {},
                concurrency=row[4],
                rate_per_min=row[5],
            ),
            status=row[6],
            circuit=row[7],
            cooldown_until=row[8],
        )
        for row in rows
    ]
