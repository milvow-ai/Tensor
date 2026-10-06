"""Any MCP server on the Farm's MCP surface, exactly as the server describes and answers it.

The tools a server lists (``farm mcp sync``) are published as ``<namespace>__<tool>`` with the server's own
title, description, input and output schema, annotations, icons and ``_meta``: nothing is added to the schema
(the optional ``_farm`` argument below is accepted but not advertised, so a client sees the server's schema
byte for byte). Calling one goes through the router as the capability ``mcp:<provider>``: pool, strategy,
health, quota, budget and run history apply, and the answer is the server's own ``CallToolResult``, all
content blocks, ``structuredContent`` and ``isError`` included. A tool that reports ``isError`` is a result
(the router does not fail over on it); only the path to the server can fail, and then the next account is
tried and the error names each account and the kind of failure.

Built on FastMCP's primitives: ``Tool`` / ``ToolResult.from_mcp_result`` for the relay, and the BM25
``search_tools`` / ``call_tool`` transform for discovery (tools that are not listed stay hidden, searchable
and callable). ``expose`` / ``mcp_direct_limit`` / ``mcp_pinned`` decide which tools are listed
(``farm.mcp.expose``).

The optional ``_farm`` argument of a call, ``{"account": "<connection id>", "strategy": "<strategy>",
"timeout_s": <seconds>}``, is removed before the call is forwarded: it pins an account, picks a strategy for
this call, or shortens the attempt timeout.

The arguments of a call are never written to the database: they travel to the executor inside a pydantic
``SecretStr`` (which every serializer masks), and every call gets a unique ``call`` id so the router can
neither cache nor collapse two identical calls (a tool may have side effects).
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import uuid4

import mcp_types
import structlog
from fastmcp import FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.context import Context
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools import Tool, ToolResult
from fastmcp.utilities.tasks import TaskConfig
from mcp_types import TextContent
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SecretStr, ValidationError

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.mcp.client import REJECTION_KEY, RESULT_KEY
from farm.gateway.middleware import current_caller
from farm.mcp import store
from farm.mcp.expose import ExposedTool, plan_exposure
from farm.registry.models import Strategy
from farm.resources.router import RouteOutcome, UnknownCapability, route

log = structlog.get_logger(__name__)

FARM_ARGUMENT = "_farm"
SEARCH_MAX_RESULTS = int(os.environ.get("FARM_MCP_SEARCH_MAX_RESULTS", "25"))

INSTRUCTIONS = (
    " The Farm also fronts MCP servers ({namespaces}): their tools are named <server>__<tool> and keep the "
    "server's own descriptions, schemas and results. search_tools(query) finds tools that are not listed, "
    "call_tool(name, arguments) runs any of them. Every call goes to the best account of that server, with "
    "failover to the next one. A call's arguments may carry _farm: {{account, strategy, timeout_s}} "
    "(all optional) to pick the account or strategy; it is removed before the server sees the call."
)


class FarmControl(BaseModel):
    """The optional ``_farm`` argument of a pass-through call."""

    model_config = ConfigDict(extra="forbid")

    account: str | None = Field(default=None, description="Connection id to use for this call")
    strategy: Strategy | None = Field(default=None, description="Strategy for this call")
    timeout_s: float | None = Field(default=None, gt=0, le=3600, description="Give up an attempt after this")


def parse_control(raw: Any) -> FarmControl:
    if raw is None:
        return FarmControl()
    try:
        return FarmControl.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'value'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
        raise ToolError(
            f"invalid {FARM_ARGUMENT}: {problems} (allowed: account, strategy, timeout_s)"
        ) from None


def failure_text(tool: str, outcome: RouteOutcome) -> str:
    """What the AI reads when no account could run the call: which, why, what to do, which run to open."""
    error = outcome.error
    if error is None:  # a bug: ok=False always carries an error
        return f"Harness Farm could not run '{tool}' (run {outcome.run_id})."
    lines = [f"Harness Farm could not run '{tool}': {error.kind}: {error.message}"]
    for attempt in error.attempts:
        who = attempt.connection_id or attempt.provider
        lines.append(f"- {who}: {attempt.outcome} ({attempt.kind}) {attempt.message}")
    if error.hint:
        lines.append(f"What to do: {error.hint}")
    lines.append(f"Run {outcome.run_id}: get_run shows every decision the Farm made.")
    return "\n".join(lines)


def relay(tool: str, outcome: RouteOutcome) -> ToolResult:
    """The caller's result: the server's own ``CallToolResult`` when an account answered."""
    if not outcome.ok:
        return ToolResult(content=[TextContent(type="text", text=failure_text(tool, outcome))], is_error=True)
    data = outcome.result or {}
    if RESULT_KEY in data:
        return ToolResult.from_mcp_result(mcp_types.CallToolResult.model_validate(data[RESULT_KEY]))
    rejection = data.get(REJECTION_KEY)
    if isinstance(rejection, dict):  # the server rejected the request itself (unknown tool, invalid params)
        text = f"{rejection.get('message', 'request rejected')} (the server's error {rejection.get('code')})"
        return ToolResult(content=[TextContent(type="text", text=text)], is_error=True)
    raise ToolError(f"the executor of '{tool}' returned neither a result nor an error")


class PassthroughTool(Tool):
    """A tool of an MCP server, published as the server describes it; calling it runs ``route``."""

    task_config: TaskConfig = TaskConfig(mode="forbidden")
    _ctx: FarmContext = PrivateAttr()
    _provider: store.McpProvider = PrivateAttr()
    _stored: store.StoredTool = PrivateAttr()

    @classmethod
    def create(cls, ctx: FarmContext, item: ExposedTool) -> PassthroughTool:
        remote = item.stored.definition
        tool = cls(
            name=item.name,
            title=remote.title,
            description=remote.description,
            parameters=remote.input_schema,
            output_schema=remote.output_schema,
            annotations=remote.annotations,
            icons=remote.icons,
            meta=remote.meta,
            execution=remote.execution,
        )
        tool._ctx = ctx
        tool._provider = item.provider
        tool._stored = item.stored
        return tool

    def to_mcp_tool(self, **overrides: Any) -> mcp_types.Tool:
        """The server's own ``Tool`` entry: FastMCP would derive a title and add ``_meta.fastmcp`` tags."""
        tool = super().to_mcp_tool(**overrides)
        remote = self._stored.definition
        if "title" not in overrides:
            tool.title = remote.title
        if "_meta" not in overrides:
            tool.meta = remote.meta
        return tool

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        args = dict(arguments)
        control = parse_control(args.pop(FARM_ARGUMENT, None))
        params: dict[str, Any] = {
            "tool": self._stored.name,
            "arguments": SecretStr(json.dumps(args)),
            "call": uuid4().hex,
        }
        if control.timeout_s is not None:
            params["timeout_s"] = control.timeout_s
        try:
            outcome = await route(
                self._ctx,
                self._provider.capability,
                params,
                strategy=control.strategy,
                pin=control.account,
                caller=current_caller.get(),
            )
        except UnknownCapability:
            raise ToolError(
                f"the Farm does not know '{self._provider.capability}' yet: restart `farm serve`"
            ) from None
        except NotImplementedError as exc:
            raise ToolError(str(exc)) from None
        return relay(self.name, outcome)


@dataclass(frozen=True)
class McpSurface:
    """What :func:`register_mcp_tools` put on the server."""

    tools: tuple[ExposedTool, ...]
    """Every pass-through tool the server can run (listed or not)."""

    @property
    def listed(self) -> tuple[ExposedTool, ...]:
        return tuple(item for item in self.tools if item.direct)


_LAST_SURFACE: McpSurface | None = None
_LAST_POOL: DbPool | None = None


def _normalize_name(name: str) -> str:
    return name.lower().replace("-", "_").replace(":", "__").replace("/", "__")


def resolve_tool_in_catalog(name: str, catalog: Sequence[Tool]) -> Tool | None:
    """Resolve a tool name against catalog tools allowing prefix omission, aliases, and hyphen/underscore."""
    for tool in catalog:
        if tool.name == name:
            return tool

    norm = _normalize_name(name)
    for tool in catalog:
        if _normalize_name(tool.name) == norm:
            return tool

    suffix_matches = [t for t in catalog if t.name.split("__", 1)[-1] == name]
    if suffix_matches:
        return suffix_matches[0]

    norm_tail = norm.split("__")[-1]
    norm_matches = [t for t in catalog if _normalize_name(t.name).split("__")[-1] == norm_tail]
    if norm_matches:
        return norm_matches[0]

    return None


async def list_server_tools(server: str, pool: DbPool | None = None) -> list[dict[str, str]]:
    """Return every tool name and one-line description for one MCP server."""
    global _LAST_SURFACE, _LAST_POOL
    p_pool = pool or _LAST_POOL

    if _LAST_SURFACE is not None:
        matches = [
            t for t in _LAST_SURFACE.tools
            if t.provider.namespace == server or t.provider.id == server
        ]
        if matches:
            return [
                {
                    "name": t.name,
                    "description": (
                        (t.stored.definition.description or "").splitlines()[0]
                        if t.stored.definition.description
                        else ""
                    ),
                }
                for t in matches
            ]

    if p_pool is not None:
        providers = await store.list_providers(p_pool, enabled_only=False)
        target_providers = [p for p in providers if p.namespace == server or p.id == server]
        if target_providers:
            p = target_providers[0]
            stored = await store.load_tools(p_pool, [p.id])
            plan = plan_exposure([p], stored, direct_limit=len(stored) + 1)
            name_map = {item.stored.name: item.name for item in plan}
            return [
                {
                    "name": name_map.get(t.name, f"{p.namespace}__{t.name}"),
                    "description": (
                        (t.definition.description or "").splitlines()[0]
                        if t.definition.description
                        else ""
                    ),
                }
                for t in stored
            ]

    return []


class FarmBM25SearchTransform(BM25SearchTransform):
    """BM25 search transform with capability discovery fixes:
    1. Returns up to max_results (25).
    2. Indexes all tools (including directly listed tools) so no tool is missing from search.
    3. Resolves unprefixed, colon/slash namespaced, and hyphen/underscore variants in call_tool and get_tool.
    """

    async def _get_visible_tools(self, ctx: Context) -> Sequence[Tool]:
        """Include all tools from catalog so directly listed tools are also searchable."""
        return await self.get_tool_catalog(ctx)

    def _make_call_tool(self) -> Tool:
        transform = self

        async def call_tool(
            name: Annotated[str, "The name of the tool to call"],
            arguments: Annotated[
                dict[str, Any] | None, "Arguments to pass to the tool"
            ] = None,
            ctx: Context = None,  # type: ignore[assignment]
        ) -> ToolResult:
            """Call a tool by name with the given arguments.

            Use this to execute tools discovered via search_tools.
            """
            if name in {transform._call_tool_name, transform._search_tool_name}:
                raise ValueError(
                    f"'{name}' is a synthetic search tool and cannot be called via the call_tool proxy"
                )
            catalog = await transform.get_tool_catalog(ctx)
            target = resolve_tool_in_catalog(name, catalog)
            if target is None:
                raise NotFoundError(f"Unknown tool: {name!r}")
            return await ctx.fastmcp.call_tool(target.name, arguments)

        return Tool.from_function(fn=call_tool, name=self._call_tool_name)

    async def get_tool(
        self, name: str, call_next: Any, *, version: Any = None
    ) -> Tool | None:
        """Intercept synthetic tool names; resolve names downstream."""
        if name == self._search_tool_name:
            return self._make_search_tool()
        if name == self._call_tool_name:
            return self._make_call_tool()
        res = await call_next(name, version=version)
        if isinstance(res, Tool):
            return res
        if self._indexed_tools:
            target = resolve_tool_in_catalog(name, self._indexed_tools)
            if target is not None and target.name != name:
                target_tool = await call_next(target.name, version=version)
                if isinstance(target_tool, Tool):
                    return target_tool
        return None


async def register_mcp_tools(
    server: FastMCP,
    ctx: FarmContext,
    *,
    direct_limit: int = 40,
    pinned: Sequence[str] = (),
) -> McpSurface | None:
    """Publish the cached tools of every enabled pass-through provider; ``None`` when there is none.

    Reads the catalogue ``farm mcp sync`` stored: starting the server never waits for an MCP server. The
    listed tools are chosen by ``farm.mcp.expose``; the others stay reachable through ``search_tools`` /
    ``call_tool``, which exist whenever a provider is configured.
    """
    providers = await store.list_providers(ctx.pool)
    if not providers:
        return None
    if "mcp" not in ctx.executors:
        log.warning("mcp.no_executor", providers=[p.id for p in providers])
    for provider in providers:
        await store.ensure_capability(ctx.pool, provider)

    stored = await store.load_tools(ctx.pool, [p.id for p in providers])
    plan = plan_exposure(providers, stored, direct_limit=direct_limit, pinned=pinned)
    native = {tool.name for tool in await server.list_tools(run_middleware=False)}
    surface: list[ExposedTool] = []
    for item in plan:
        if item.name in native:
            log.warning("mcp.name_collision", tool=item.name, provider=item.provider.id)
            continue
        server.add_tool(PassthroughTool.create(ctx, item))
        surface.append(item)

    @server.tool(name="list_server_tools", annotations={"readOnlyHint": True})
    async def list_server_tools_tool(server: str) -> list[dict[str, str]]:
        """List all tools and one-line descriptions for one MCP server."""
        return await list_server_tools(server, pool=ctx.pool)

    listed = native | {item.name for item in surface if item.direct}
    server.add_transform(
        FarmBM25SearchTransform(max_results=SEARCH_MAX_RESULTS, always_visible=sorted(listed))
    )
    namespaces = ", ".join(sorted(provider.namespace for provider in providers))
    server.instructions = (server.instructions or "") + INSTRUCTIONS.format(namespaces=namespaces)
    log.info(
        "mcp.registered",
        providers=len(providers),
        tools=len(surface),
        listed=sum(1 for item in surface if item.direct),
    )
    result_surface = McpSurface(tuple(surface))
    global _LAST_SURFACE, _LAST_POOL
    _LAST_SURFACE = result_surface
    _LAST_POOL = ctx.pool
    return result_surface


__all__ = [
    "FARM_ARGUMENT",
    "FarmBM25SearchTransform",
    "FarmControl",
    "McpSurface",
    "PassthroughTool",
    "failure_text",
    "list_server_tools",
    "parse_control",
    "register_mcp_tools",
    "relay",
    "resolve_tool_in_catalog",
]
