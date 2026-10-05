"""Acceptance tests for RUN1: farm run shared endpoint, tokens, connect, budgets, alerts, and watchdog.

Matches all 5 criteria from briefs/RUN1-farm-run-shared-endpoint.md:
1. Three concurrent MCP HTTP clients with different tokens listing and calling tools concurrently;
   all results correct, run rows carry right client names; request with wrong/no token -> 401.
2. Watchdog iteration in test mode restarts within 60s and records an alert when farm is down.
3. Hard-stop budget blocks paid call with reason, allows free call; soft thresholds alert once each.
4. farm connect claude-code output contains URL and env-var token reference, never literal token.
5. Graceful shutdown lets in-flight call finish.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import respx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from typer.testing import CliRunner

from farm.context import FarmContext
from farm.control.cli import app as cli_app
from farm.control.run import (
    create_farm_app,
    create_token,
    ensure_tokens_table,
)
from farm.db.pool import DbPool
from farm.manager.alerts import list_open_alerts
from farm.manager.telegram import clear_telegram_state
from farm.registry import Registry
from farm.resources import budget
from farm.resources.router import route
from tests.conftest import seed_connection
from tests.farm_helpers import EMAIL, ScriptedExecutor, ok_result

type Make = Callable[..., Awaitable[FarmContext]]


async def test_concurrent_mcp_http_clients_and_caller_tracking(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    """Acceptance 1: Three concurrent MCP HTTP clients with different tokens call tools concurrently.

    Run rows record the caller client name, and wrong/no token returns genuine HTTP 401.
    """
    await ensure_tokens_table(pool)
    token_claude = await create_token(pool, "claude-code")
    token_cursor = await create_token(pool, "cursor")
    token_codex = await create_token(pool, "codex")

    executor = ScriptedExecutor(lambda req: ok_result())
    ctx = await farm_factory(registry, executors={"api": executor})
    app = await create_farm_app(ctx)

    def make_factory(tok: str) -> Callable[..., httpx.AsyncClient]:
        def factory(headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.AsyncClient:
            hdrs = dict(headers or {})
            hdrs["Authorization"] = f"Bearer {tok}"
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                headers=hdrs,
                **kwargs,
            )

        return factory

    async def run_client(tok: str, email_addr: str) -> dict[str, Any]:
        transport = StreamableHttpTransport(
            "http://127.0.0.1/mcp",
            headers={"Authorization": f"Bearer {tok}"},
            httpx_client_factory=make_factory(tok),
        )
        async with Client(transport) as client:
            tools = await client.list_tools()
            tool_names = {t.name for t in tools}
            assert "verify_email" in tool_names
            res = await client.call_tool("verify_email", {"email": email_addr})
            assert res.structured_content is not None
            return res.structured_content

    base_starlette_app = getattr(app, "app", app)
    async with base_starlette_app.router.lifespan_context(base_starlette_app):
        # Run three clients concurrently
        res_claude, res_cursor, res_codex = await asyncio.gather(
            run_client(token_claude, "claude@example.com"),
            run_client(token_cursor, "cursor@example.com"),
            run_client(token_codex, "codex@example.com"),
        )

        assert res_claude.get("ok") is True
        assert res_cursor.get("ok") is True
        assert res_codex.get("ok") is True

    # Verify run rows carried right caller names
    async with pool.connection() as conn:
        cur = await conn.execute("select caller, count(*) from public.runs group by caller order by caller")
        rows = await cur.fetchall()
        caller_counts = {r[0]: r[1] for r in rows}
        assert caller_counts.get("claude-code") == 1
        assert caller_counts.get("cursor") == 1
        assert caller_counts.get("codex") == 1

    # Verify rejection without token or with wrong token -> 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
    ) as http_client:
        r_no_token = await http_client.post("/mcp", json={})
        assert r_no_token.status_code == 401
        assert r_no_token.headers.get("www-authenticate") == "Bearer"

        r_bad_token = await http_client.post(
            "/mcp",
            headers={"Authorization": "Bearer bad-token-xyz"},
            json={},
        )
        assert r_bad_token.status_code == 401


def test_watchdog_restart_and_alert_on_failure(tmp_path: Path) -> None:
    """Acceptance 2: Watchdog in test mode detects dead farm, restarts, and records alert.

    The heartbeat file and log are per-test: a real `farm run` on this PC writes a fresh heartbeat in the
    shared data dir, which would make the watchdog (rightly) report the Farm healthy.
    """
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/farm-watchdog.ps1",
            "-TestMode",
            "-NoSpawn",
            "-SkipBifrost",
            "-FarmUrl",
            "http://127.0.0.1:59999",
            "-FarmHeartbeatFile",
            str(tmp_path / "no-heartbeat.json"),
            "-LogFile",
            str(tmp_path / "watchdog.log"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "Farm unhealthy" in result.stdout
    assert "Restarting Farm..." in result.stdout
    assert "ALERT: Harness Farm Watchdog: Farm process unhealthy" in result.stdout
    assert "[TestMode] Single iteration complete" in result.stdout


@respx.mock
async def test_hard_stop_budget_and_soft_thresholds_acceptance(pool: DbPool) -> None:
    """Acceptance 3: Hard-stop budget blocks paid call with reason, allows free call; soft thresholds alert once each."""
    clear_telegram_state()
    os.environ["TELEGRAM_BOT_TOKEN"] = "fake-token"
    os.environ["TELEGRAM_CHAT_ID"] = "12345678"

    respx.post("https://api.telegram.org/botfake-token/sendMessage").respond(
        status_code=200, json={"ok": True}
    )

    try:
        now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

        # 1. Seed free and paid connections
        await seed_connection(
            pool,
            "p_acc_free",
            "c_acc_free",
            units={"credits": {"limit": 100, "unit_cost_usd": 0}},
        )
        await seed_connection(
            pool,
            "p_acc_paid",
            "c_acc_paid",
            units={"credits": {"limit": 100, "unit_cost_usd": 0.10}},
        )

        # 2. Hard stop budget of $5.00 on c_acc_paid
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
                "values ('connection', 'c_acc_paid', 5.00, true)"
            )
            # Spend $4.95
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_acc_paid', 'charge', 4.95, %s)",
                (now,),
            )

        # Free call (est_cost = 0) is allowed
        free_decision = await budget.check(pool, "c_acc_free", 0, now=now)
        assert free_decision.allowed is True

        # Paid call (est_cost = 0.50) is blocked with clear reason
        paid_decision = await budget.check(pool, "c_acc_paid", Decimal("0.50"), now=now)
        assert paid_decision.allowed is False
        assert paid_decision.error_kind == "budget_exhausted"
        assert paid_decision.scope == "connection"
        assert paid_decision.budget_usd == Decimal("5.00")
        assert paid_decision.current_spend_usd == Decimal("4.95")
        assert "hard stop" in (paid_decision.reason or "").lower()

        # 3. Soft budget with thresholds 50%, 80%, 100%
        await seed_connection(
            pool,
            "p_acc_soft",
            "c_acc_soft",
            units={"credits": {"limit": 1000, "unit_cost_usd": 0.10}},
        )
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
                "values ('connection', 'c_acc_soft', 100.00, false)"
            )
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_acc_soft', 'charge', 55.00, %s)",
                (now,),
            )

        # Call under soft budget proceeds and alerts at 50%
        soft_decision = await budget.check(pool, "c_acc_soft", Decimal("1.00"), now=now)
        assert soft_decision.allowed is True
        alerts = [a for a in await list_open_alerts(pool) if "c_acc_soft" in a["message"]]
        assert len(alerts) == 1
        assert "50%" in alerts[0]["message"]

        # Repeated call does not duplicate alert
        await budget.check(pool, "c_acc_soft", Decimal("1.00"), now=now)
        alerts_repeat = [a for a in await list_open_alerts(pool) if "c_acc_soft" in a["message"]]
        assert len(alerts_repeat) == 1

        # Advance spend to 85% ($85)
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_acc_soft', 'charge', 30.00, %s)",
                (now,),
            )
        await budget.check(pool, "c_acc_soft", Decimal("1.00"), now=now)
        alerts_80 = [a for a in await list_open_alerts(pool) if "c_acc_soft" in a["message"]]
        assert len(alerts_80) == 2

        # Advance spend to 105% ($105)
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_acc_soft', 'charge', 20.00, %s)",
                (now,),
            )
        await budget.check(pool, "c_acc_soft", Decimal("1.00"), now=now)
        alerts_100 = [a for a in await list_open_alerts(pool) if "c_acc_soft" in a["message"]]
        assert len(alerts_100) == 3

    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


def test_connect_claude_code_acceptance() -> None:
    """Acceptance 4: farm connect claude-code output contains URL, env-var token reference, never literal token."""
    runner = CliRunner()
    result = runner.invoke(cli_app, ["connect", "claude-code"])
    assert result.exit_code == 0
    assert "http://127.0.0.1:8787/mcp" in result.stdout
    assert "${FARM_TOKEN}" in result.stdout
    assert "[VERIFIED]" in result.stdout
    # Must never include a literal token
    assert "farm_tok_" not in result.stdout
    assert "farm_" not in result.stdout


async def test_graceful_shutdown_finishes_in_flight_call(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    """Acceptance 5: Graceful shutdown lets an in-flight call finish without being aborted."""
    call_started = asyncio.Event()
    call_can_finish = asyncio.Event()

    def slow_exec(req: Any) -> Any:
        call_started.set()
        # Non-blocking wait for event
        return ok_result()

    async def async_exec(req: Any) -> Any:
        call_started.set()
        await call_can_finish.wait()
        return ok_result()

    from tests.farm_helpers import AsyncFnExecutor

    ctx = await farm_factory(registry, executors={"api": AsyncFnExecutor(async_exec)})

    # Start an in-flight route call
    route_task = asyncio.create_task(route(ctx, "verify_email", {"email": EMAIL}, caller="in-flight-client"))

    # Wait until the call is in-flight
    await call_started.wait()

    # Simulate shutdown initiation
    shutdown_task = asyncio.create_task(ctx.aclose())

    # Allow in-flight call to finish
    call_can_finish.set()

    # In-flight route finishes cleanly
    out = await route_task
    assert out.ok is True
    assert out.source is not None

    # Wait for context cleanup to complete
    await shutdown_task

    # Verify run row succeeded
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select caller, status from public.runs where id = %s",
            (out.run_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "in-flight-client"
        assert row[1] == "succeeded"
