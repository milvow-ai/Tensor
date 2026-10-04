"""M1 acceptance: the real thing, as Claude Code starts it: ``farm serve`` as a child process speaking MCP on stdio.

No in-memory shortcuts: a subprocess, its stdout carrying the protocol, its stderr carrying the logs, Postgres
behind it. The provider side is not mocked (a subprocess cannot be), so the call that is made is served from the
cache: the answer is planted in ``capability_requests`` exactly as a previous provider call would have left it,
which exercises the whole server path except the HTTP request to the vendor (covered by the respx tests).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from alembic import command
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from psycopg.types.json import Jsonb
from typer.testing import CliRunner

from farm.control.cli import alembic_config, app
from farm.resources.router import request_hash
from tests.conftest import FIXTURE_REGISTRY
from tests.farm_helpers import EMAIL

STARTUP_TIMEOUT_S = 120


async def talk(url: str, email: str) -> dict[str, Any]:
    transport = StdioTransport(
        command=sys.executable,
        args=["-c", "from farm.control.cli import app; app()", "serve"],
        env={**os.environ, "FARM_DB_URL": url, "FARM_LOG_LEVEL": "INFO"},
    )
    async with Client(transport) as client:
        names = sorted(t.name for t in await client.list_tools())
        reply = await client.call_tool("verify_email", {"email": email})
        capacity = await client.call_tool("get_capacity", {"capability": "verify_email"})
        run_id = (reply.structured_content or {})["run_id"]
        run = await client.call_tool("get_run", {"run_id": run_id})
    return {
        "tools": names,
        "envelope": reply.structured_content,
        "capacity": capacity.structured_content,
        "run": run.structured_content,
    }


def test_farm_serve_over_stdio_lists_the_tools_and_answers_verify_email(
    scratch_db: Callable[[], str], monkeypatch: pytest.MonkeyPatch, provider_keys: None
) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    monkeypatch.setenv("FARM_DB_URL", url)
    synced = CliRunner().invoke(app, ["registry", "sync", str(FIXTURE_REGISTRY)])
    assert synced.exit_code == 0, synced.stderr

    answer = {
        "data": {
            "email": EMAIL,
            "status": "valid",
            "sub_status": None,
            "provider": "reoon",
            "checked_at": "2026-10-04T12:00:00Z",
        },
        "provider": "reoon",
        "connection_id": "reoon-01",
        "found": True,
    }
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "insert into capability_requests (request_hash, capability, params, status, result, expires_at) "
            "values (%s, 'verify_email', %s, 'succeeded', %s, %s)",
            (
                request_hash("verify_email", {"email": EMAIL}),
                Jsonb({"email": EMAIL}),
                Jsonb(answer),
                datetime.now(UTC) + timedelta(days=30),
            ),
        )

    seen = asyncio.run(asyncio.wait_for(talk(url, EMAIL), STARTUP_TIMEOUT_S))

    assert seen["tools"] == ["get_capacity", "get_run", "get_usage", "list_resources", "verify_email"]
    envelope = seen["envelope"]
    assert envelope["ok"] is True and envelope["result"] == answer["data"]
    assert envelope["source"] == {"provider": "reoon", "connection_id": "reoon-01", "cached": True}
    assert envelope["cost"] == {"usd": 0.0, "units": {}}
    assert [p["provider_id"] for p in seen["capacity"]["pools"]] == ["reoon", "zerobounce"]
    run = seen["run"]
    assert run["status"] == "succeeded" and run["cached"] is True and run["caller"] == "mcp:mcp"
    assert [e["kind"] for e in run["events"]] == ["cache_hit"]
    json.dumps(seen)  # everything that crossed the pipe is plain JSON
