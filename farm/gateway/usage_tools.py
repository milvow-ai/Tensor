"""Read-only usage monitoring MCP tool registration."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from farm.context import FarmContext
from farm.manager.ai_usage import usage_report


def register_usage_tools(server: FastMCP, ctx: FarmContext) -> None:
    @server.tool(annotations={"readOnlyHint": True})
    async def get_ai_usage() -> dict[str, Any]:
        """Report observed daily/monthly AI use, known costs, and missing metering.

        Provider allowance snapshots are included when available.
        """
        return await usage_report(ctx.pool, ctx.clock())
