"""The Harness Farm MCP server: tools generated from the capability table, plus read-only infra tools.

Tools
* one tool per capability in the ``capabilities`` table that has models in ``CAPABILITY_MODELS``. Its input
  schema is the capability's input model plus an optional ``routing_strategy``; its result is the standard
  envelope (CONTEXT section 4): ``{ok, result, error, run_id, source, cost}``. A failed call is
  ``ok = false`` with ``error`` (kind, message, hint, attempts) and ``isError`` set, never a protocol-level
  exception, so the agent can read why and what to do.
* ``get_capacity(capability?)``, ``list_resources()``, ``get_run(run_id)``, ``get_usage(days)``: read-only.
  No tool changes the Farm (pause, budgets, accounts): those are for the owner, through the Console or CLI.

``build_server`` reads the capability table when the server starts; a registry change that adds a capability
shows up after a restart (``farm serve`` is cheap to restart; the tool list is not hot-reloaded).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, ToolResult
from pydantic import BaseModel, Field, PrivateAttr, create_model

from farm.capabilities.schemas import CAPABILITY_MODELS
from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.gateway.mcp_tools import register_mcp_tools
from farm.gateway.middleware import (
    AuthMiddleware,
    PolicyCheck,
    PolicyMiddleware,
    TrajectoryMiddleware,
    current_caller,
)
from farm.registry.models import MCP_CAPABILITY_PREFIX, STRATEGIES
from farm.resources import reports
from farm.resources.router import RouteOutcome, route
from farm.resources.trajectory import fetch_run

log = structlog.get_logger(__name__)

SERVER_NAME = "harness-farm"
INFRA_TOOLS = ("get_capacity", "list_resources", "get_run", "get_usage")
ROUTING_ARGUMENT = "routing_strategy"

"""Optional tool argument: the strategy for this call. Own name, because capability inputs may have a
``strategy`` field of their own (PageSpeed's mobile/desktop)."""
ROUTING_PROPERTY: dict[str, Any] = {
    "type": "string",
    "enum": list(STRATEGIES),
    "description": "How to pick between accounts of one provider (default: the capability's strategy).",
}

INSTRUCTIONS = (
    "Harness Farm routes capability calls (verify an email, ...) to the best provider account, tracks quota "
    "and cost, falls back when a provider fails, and caches answers. Every capability tool returns the same "
    "envelope: ok, result, error (kind, message, hint, attempts), run_id, source (provider, connection_id, "
    "cached) and cost. Identical requests are answered from cache at no cost. Use get_run(run_id) to see why "
    "the Farm chose what it chose, get_capacity to see what is left, get_usage for spend."
)


def envelope_schema(output: type[BaseModel]) -> dict[str, Any]:
    """JSON schema of the envelope with ``result`` typed as the capability's output model."""
    model = create_model(f"{output.__name__}Envelope", __base__=RouteOutcome, result=(output | None, None))
    return model.model_json_schema(mode="serialization")


class CapabilityTool(Tool):
    """The MCP tool of one capability; calling it runs ``route`` for it."""

    _ctx: FarmContext = PrivateAttr()

    @classmethod
    def create(
        cls,
        ctx: FarmContext,
        name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
    ) -> CapabilityTool:
        parameters = input_model.model_json_schema()
        properties = parameters.setdefault("properties", {})
        if ROUTING_ARGUMENT in properties:
            raise ValueError(f"capability '{name}': the input field '{ROUTING_ARGUMENT}' is reserved")
        properties[ROUTING_ARGUMENT] = ROUTING_PROPERTY
        tool = cls(
            name=name,
            description=description,
            parameters=parameters,
            output_schema=envelope_schema(output_model),
        )
        tool._ctx = ctx
        return tool

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        args = dict(arguments)
        strategy = args.pop(ROUTING_ARGUMENT, None)
        outcome = await route(self._ctx, self.name, args, strategy=strategy, caller=current_caller.get())
        return ToolResult(structured_content=outcome.envelope(), is_error=not outcome.ok)


def _register_infra_tools(server: FastMCP, ctx: FarmContext) -> None:
    @server.tool(annotations={"readOnlyHint": True})
    async def get_capacity(capability: str | None = None) -> dict[str, Any]:
        """What is left per provider pool and account: state, circuit, cooldown, remaining and next reset.

        Pass a capability name to see only the pools on its route, in route order.
        """
        pools = await reports.capacity_report(ctx.pool, ctx.clock(), capability)
        return {"pools": [p.model_dump(mode="json") for p in pools]}

    @server.tool(annotations={"readOnlyHint": True})
    async def list_resources() -> dict[str, Any]:
        """The inventory: capabilities with their routes, providers with their accounts and units."""
        return await reports.list_resources(ctx.pool)

    @server.tool(annotations={"readOnlyHint": True})
    async def get_run(run_id: str) -> dict[str, Any]:
        """The trajectory of one call (the run_id of an envelope): every decision the Farm made, in order."""
        try:
            wanted = UUID(run_id)
        except ValueError:
            raise ToolError("run_id must be a UUID, as returned in an envelope") from None
        run = await fetch_run(ctx.pool, wanted)
        if run is None:
            raise ToolError(f"there is no run {run_id}")
        return run.model_dump(mode="json")

    @server.tool(annotations={"readOnlyHint": True})
    async def get_usage(days: Annotated[int, Field(ge=1, le=366)] = 30) -> dict[str, Any]:
        """What the last N days consumed and cost, per account and unit, and how many runs ended how."""
        return await reports.usage_report(ctx.pool, ctx.clock(), days)

    @server.tool(annotations={"readOnlyHint": True})
    async def list_ais() -> dict[str, Any]:
        """List AI providers, accounts, status, models, cooldown/reset times, and today's calls."""
        return await list_ai_accounts(ctx.pool, ctx.clock())

    @server.tool()
    async def ask_ai_batch(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        """Run multiple AI tasks across the AI pool respecting gates and concurrency."""

        async def run_one(task_args: dict[str, Any]) -> dict[str, Any]:
            args = dict(task_args)
            strategy = args.pop(ROUTING_ARGUMENT, None)
            outcome = await route(ctx, "ask_ai", args, strategy=strategy, caller=current_caller.get())
            return outcome.envelope()

        envelopes = await asyncio.gather(*(run_one(t) for t in tasks))
        all_ok = all(e.get("ok", False) for e in envelopes)
        return {"ok": all_ok, "tasks": list(envelopes)}


async def list_ai_accounts(pool: DbPool, now: datetime) -> dict[str, Any]:
    """Report all AI pools and their accounts, status, models, cooldown/reset times, and today's calls."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select id, name, enabled, default_strategy from public.providers where kind = 'ai' order by id"
        )
        providers = await cur.fetchall()

        cur = await conn.execute(
            """
            select c.id, c.provider_id, c.label, c.auth_ref, c.priority, c.concurrency, c.status, c.meta,
                   coalesce(h.circuit, 'closed'), h.cooldown_until, h.last_error_kind, h.last_error,
                   u.next_reset_at
            from public.connections c
            join public.providers p on p.id = c.provider_id
            left join public.connection_health h on h.connection_id = c.id
            left join (
                select connection_id, max(next_reset_at) as next_reset_at
                from public.consumption_units
                group by connection_id
            ) u on u.connection_id = c.id
            where p.kind = 'ai'
            order by c.provider_id, c.priority, c.id
            """
        )
        connections = await cur.fetchall()

        cur = await conn.execute(
            """
            select connection_id, count(*)
            from public.runs
            where started_at >= %s and connection_id is not null
            group by connection_id
            """,
            (today_start,),
        )
        call_counts: dict[str, int] = dict(await cur.fetchall())

    ais: list[dict[str, Any]] = []
    accounts_by_provider: dict[str, list[dict[str, Any]]] = {}
    for c in connections:
        conn_id = c[0]
        prov_id = c[1]
        label = c[2]
        status = c[6]
        meta = c[7] or {}
        circuit = c[8]
        cooldown_until = c[9]
        last_error = c[11]
        next_reset_at = c[12]

        models = meta.get("models")
        if isinstance(models, str):
            models_list = [models]
        elif isinstance(models, list):
            models_list = [str(m) for m in models]
        else:
            m = meta.get("model")
            models_list = [str(m)] if m else []

        reset_time = next_reset_at or cooldown_until
        reset_str = reset_time.isoformat() if reset_time else None

        accounts_by_provider.setdefault(prov_id, []).append({
            "id": conn_id,
            "provider_id": prov_id,
            "label": label,
            "status": status,
            "models": models_list,
            "circuit": circuit,
            "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
            "reset_at": reset_str,
            "next_reset_at": reset_str,
            "todays_calls": call_counts.get(conn_id, 0),
            "last_error": last_error,
        })

    for p in providers:
        p_id = p[0]
        ais.append({
            "id": p_id,
            "name": p[1],
            "enabled": p[2],
            "default_strategy": p[3],
            "accounts": accounts_by_provider.get(p_id, []),
        })

    all_accounts = [acc for p_accs in accounts_by_provider.values() for acc in p_accs]
    return {"ais": ais, "accounts": all_accounts}



async def build_server(
    ctx: FarmContext,
    *,
    token: str | None = None,
    policy: PolicyCheck | None = None,
    mcp_direct_limit: int = 40,
    mcp_pinned: Sequence[str] = (),
) -> FastMCP:
    """The ``harness-farm`` server for ``ctx``.

    ``token``: bearer token required over HTTP (stdio is local trust and ignores it). ``policy``: optional
    gate ``(caller, tool, arguments) -> reason to refuse | None``. ``mcp_direct_limit`` / ``mcp_pinned``:
    ``settings.mcp_direct_limit`` / ``settings.mcp_pinned`` of the registry (which MCP tools are listed).
    """
    server = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    server.add_middleware(AuthMiddleware(token))
    server.add_middleware(PolicyMiddleware(policy))
    server.add_middleware(TrajectoryMiddleware())

    async with ctx.pool.connection() as conn:
        cur = await conn.execute("select name, description from public.capabilities order by name")
        capabilities = await cur.fetchall()

    for name, description in capabilities:
        if name.startswith(MCP_CAPABILITY_PREFIX):
            continue  # a pass-through capability: its tools come from register_mcp_tools
        models = CAPABILITY_MODELS.get(name)
        if models is None:
            log.info("gateway.capability_without_tool", capability=name, reason="no input/output models yet")
            continue
        server.add_tool(CapabilityTool.create(ctx, name, description or name, *models))
    _register_infra_tools(server, ctx)
    await register_mcp_tools(server, ctx, direct_limit=mcp_direct_limit, pinned=mcp_pinned)
    return server
