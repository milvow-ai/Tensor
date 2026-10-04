"""MCP smoke test: start ``uv run farm serve`` over stdio, list its tools, check the ones the Farm needs.

Makes no provider call and changes nothing. Needs a migrated database with the registry synced (otherwise the
capability tools do not exist and this says so):

    uv run farm db migrate && uv run farm registry sync && uv run python scripts/mcp_smoke.py

Exit code 0 = every expected tool is there (and no admin tool is), 1 = anything else.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = ("verify_email", "get_capacity", "list_resources", "get_run", "get_usage")
FORBIDDEN_FRAGMENTS = (
    "pause",
    "resume",
    "budget",
    "add_connection",
    "update_connection",
    "remove_connection",
    "set_",
)
STARTUP_TIMEOUT_S = 180


async def smoke() -> list[str]:
    """Return the problems found (empty = healthy)."""
    transport = StdioTransport(
        command="uv", args=["run", "farm", "serve"], cwd=str(ROOT), env=dict(os.environ)
    )
    async with Client(transport) as client:
        tools = {t.name: t for t in await client.list_tools()}
        print(f"farm serve is up; {len(tools)} tools: {', '.join(sorted(tools))}")
        problems = [f"missing tool: {name}" for name in EXPECTED if name not in tools]
        problems += [
            f"admin-looking tool exposed to agents: {name}"
            for name in tools
            if any(fragment in name for fragment in FORBIDDEN_FRAGMENTS)
        ]
        if "verify_email" in tools:
            schema = tools["verify_email"].input_schema
            if "email" not in schema.get("properties", {}):
                problems.append("verify_email has no 'email' input")
        else:
            problems.append("run `uv run farm db migrate` and `uv run farm registry sync` first")
        if "get_capacity" in tools:  # a database round trip through the real protocol; calls no provider
            capacity = (await client.call_tool("get_capacity", {})).structured_content or {}
            pools = capacity.get("pools", [])
            print(f"get_capacity answered: {len(pools)} pools")
            if not pools:
                problems.append("get_capacity returned no pools (is the registry synced?)")
    return problems


def main() -> int:
    try:
        problems = asyncio.run(asyncio.wait_for(smoke(), STARTUP_TIMEOUT_S))
    except Exception as exc:  # the server did not start, crashed, or spoke something that is not MCP
        print(f"FAIL: could not talk to `farm serve`: {type(exc).__name__}: {exc}")
        return 1
    for problem in problems:
        print(f"FAIL: {problem}")
    print("mcp smoke: " + ("FAILED" if problems else "ok"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
