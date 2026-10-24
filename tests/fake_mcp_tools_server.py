"""A fake MCP server whose tool names mix hyphens, underscores and both (Q0A, defect D2).

The names are the shapes real servers publish: Clay's ``get-credits-available`` / ``list_subroutines``, a mixed
``get_task-context``, and the pair ``get-user_id`` / ``get_user-id`` that differ only in where the hyphen is.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

HYPHEN = "get-credits-available"
UNDERSCORE = "list_subroutines"
MIXED = "get_task-context"
TWIN_HYPHEN = "get-user_id"
TWIN_UNDERSCORE = "get_user-id"

APP_ONLY = "get-app-credits"
"""Declares ``_meta.ui.visibility = ["app"]`` (MCP Apps): FastMCP keeps such a tool out of a model-facing catalog."""

TOOL_NAMES = (HYPHEN, UNDERSCORE, MIXED, TWIN_HYPHEN, TWIN_UNDERSCORE, APP_ONLY)


LISTED_TOOL_NAMES = ("list-widgets", "get_widget")


def create_fake_tools_server(
    name: str = "fake-tools", *, tools: tuple[str, ...] = TOOL_NAMES
) -> FastMCP:
    """Every tool answers ``{"tool": <its own name>}`` so a test can see which one ran."""
    server = FastMCP(name)

    def register(
        tool_name: str, description: str, *, read_only: bool, meta: dict[str, Any] | None = None
    ) -> None:
        def handler() -> dict[str, Any]:
            return {"tool": tool_name}

        server.tool(
            handler,
            name=tool_name,
            description=description,
            annotations={"readOnlyHint": read_only},
            meta=meta,
        )

    catalog = {
        HYPHEN: ("Get the credits available in the workspace.", True),
        UNDERSCORE: ("List the subroutines of the workspace.", True),
        MIXED: ("Get the context of one task.", True),
        TWIN_HYPHEN: ("Get a user by id (hyphen first).", False),
        TWIN_UNDERSCORE: ("Get a user by id (underscore first).", False),
        APP_ONLY: ("Get the credits shown by the app.", True),
        "list-widgets": ("List the widgets.", True),
        "get_widget": ("Get one widget.", True),
    }
    for tool_name in tools:
        description, read_only = catalog[tool_name]
        register(
            tool_name,
            description,
            read_only=read_only,
            meta={"ui": {"visibility": ["app"]}} if tool_name == APP_ONLY else None,
        )
    return server
