"""Which MCP tools the Farm shows as tools of their own, and under which names.

Pure planning, no I/O (the gateway applies the plan, ``farm mcp sync`` applies :func:`is_allowed`):

* ``tools.allow`` / ``tools.deny`` (fnmatch globs, case-sensitive) decide which of a server's tools exist for
  the Farm at all; a denied tool is neither listed, searchable nor callable;
* ``expose`` per provider: ``direct`` (every tool is its own tool), ``discovery`` (none is: they are found
  with ``search_tools`` and run with ``call_tool``) or ``auto``;
* ``auto`` is direct while the MCP tools of every ``direct`` and ``auto`` provider together number at most
  ``settings.mcp_direct_limit`` (an AI that is shown hundreds of tools picks worse and pays for every one in
  context); beyond that the ``auto`` providers fall back to discovery, except for the tools named in
  ``settings.mcp_pinned`` (exposed names, globs allowed).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from farm.mcp.naming import exposed_names
from farm.mcp.store import McpProvider, StoredTool
from farm.registry.models import McpToolsSpec


@dataclass(frozen=True)
class ExposedTool:
    name: str
    """The name on the Farm's MCP surface: ``<namespace>__<tool>``."""
    provider: McpProvider
    stored: StoredTool
    direct: bool

    @property
    def remote_name(self) -> str:
        return self.stored.name


def is_allowed(name: str, tools: McpToolsSpec) -> bool:
    """``name`` matches an ``allow`` glob and no ``deny`` glob."""
    return any(fnmatchcase(name, pattern) for pattern in tools.allow) and not any(
        fnmatchcase(name, pattern) for pattern in tools.deny
    )


def plan_exposure(
    providers: Sequence[McpProvider],
    stored: Sequence[StoredTool],
    *,
    direct_limit: int,
    pinned: Sequence[str] = (),
) -> list[ExposedTool]:
    """Every allowed tool with its exposed name and whether it is listed directly, ordered by name."""
    by_provider: dict[str, list[StoredTool]] = defaultdict(list)
    for tool in stored:
        by_provider[tool.provider].append(tool)

    allowed: list[tuple[McpProvider, StoredTool, str]] = []
    for provider in providers:
        tools = [t for t in by_provider.get(provider.id, []) if is_allowed(t.name, provider.spec.tools)]
        names = exposed_names(provider.namespace, (t.name for t in tools))
        allowed.extend((provider, tool, names[tool.name]) for tool in tools)

    candidates = sum(1 for provider, _, _ in allowed if provider.spec.expose != "discovery")
    auto_is_direct = candidates <= direct_limit

    def listed(provider: McpProvider, name: str) -> bool:
        match provider.spec.expose:
            case "direct":
                return True
            case "discovery":
                return False
            case _:
                return auto_is_direct or any(fnmatchcase(name, pattern) for pattern in pinned)

    plan = [ExposedTool(name, provider, tool, listed(provider, name)) for provider, tool, name in allowed]
    return sorted(plan, key=lambda item: item.name)
