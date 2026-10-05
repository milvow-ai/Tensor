"""Tests for budget hard-stop enforcement, soft alerts, and router integration.

Covers:
- Free calls (price = 0) are never blocked by budget even if budget is 0 or exhausted.
- Paid calls exceeding hard-stop budget (connection, provider, or global) are refused
  with clear reason: 'budget_exhausted', scope, current spend, and limit.
- Soft budgets (hard_stop=False) emit alerts at 50%, 80%, and 100% thresholds once per period.
- Router integration: router honors budget.check() before reserving paid calls.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import respx

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.manager.alerts import list_open_alerts
from farm.manager.telegram import clear_telegram_state
from farm.registry import Registry
from farm.resources import budget
from farm.resources.router import route
from tests.conftest import seed_connection
from tests.farm_helpers import EMAIL, ScriptedExecutor, ok_result


@pytest.fixture(autouse=True)
def _isolate_telegram_in_budget_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    clear_telegram_state()


async def test_free_call_allowed_when_budget_zero_or_exhausted(pool: DbPool) -> None:
    """Free calls (est_cost <= 0) are allowed even when budget is $0 or fully spent."""
    await seed_connection(
        pool,
        "p_free_test",
        "c_free_test",
        units={"credits": {"limit": 100, "unit_cost_usd": 0}},
    )
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    # Set connection budget to 0 with hard stop
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
            "values ('connection', 'c_free_test', 0, true)"
        )
        # Also simulate existing spend
        await conn.execute(
            "insert into public.billing_events (connection_id, kind, amount_usd, at) "
            "values ('c_free_test', 'charge', 10.00, %s)",
            (now,),
        )

    # Free call with est_cost = 0 must be allowed
    decision = await budget.check(pool, "c_free_test", 0, now=now)
    assert decision.allowed is True
    assert bool(decision) is True
    assert decision.error_kind is None


async def test_paid_call_blocked_when_connection_hard_stop_exceeded(pool: DbPool) -> None:
    """Paid calls exceeding connection hard-stop budget are refused with details."""
    await seed_connection(
        pool,
        "p_paid_conn",
        "c_paid_conn",
        units={"credits": {"limit": 100, "unit_cost_usd": 0.05}},
    )
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    # Set connection budget to $10.00 with hard stop
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
            "values ('connection', 'c_paid_conn', 10.00, true)"
        )
        # Spend $9.80
        await conn.execute(
            "insert into public.billing_events (connection_id, kind, amount_usd, at) "
            "values ('c_paid_conn', 'charge', 9.80, %s)",
            (now,),
        )

    # Call with est_cost = 0.50 -> 9.80 + 0.50 = 10.30 > 10.00
    decision = await budget.check(pool, "c_paid_conn", Decimal("0.50"), now=now)
    assert decision.allowed is False
    assert decision.error_kind == "budget_exhausted"
    assert decision.scope == "connection"
    assert decision.budget_usd == Decimal("10.00")
    assert decision.current_spend_usd == Decimal("9.80")
    assert decision.estimated_cost_usd == Decimal("0.50")
    assert "hard stop" in (decision.reason or "").lower()
    assert "connection" in (decision.reason or "").lower()


async def test_paid_call_blocked_when_provider_hard_stop_exceeded(pool: DbPool) -> None:
    """Paid calls exceeding provider hard-stop budget are refused."""
    await seed_connection(
        pool,
        "p_prov_budget",
        "c_prov_1",
        units={"credits": {"limit": 100, "unit_cost_usd": 0.10}},
    )
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    # Set provider budget to $20.00 with hard stop
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
            "values ('provider', 'p_prov_budget', 20.00, true)"
        )
        # Spend $19.50 under c_prov_1
        await conn.execute(
            "insert into public.billing_events (connection_id, kind, amount_usd, at) "
            "values ('c_prov_1', 'charge', 19.50, %s)",
            (now,),
        )

    # Call with est_cost = 1.00 -> 19.50 + 1.00 = 20.50 > 20.00
    decision = await budget.check(pool, "c_prov_1", Decimal("1.00"), now=now)
    assert decision.allowed is False
    assert decision.error_kind == "budget_exhausted"
    assert decision.scope == "provider"
    assert decision.budget_usd == Decimal("20.00")
    assert decision.current_spend_usd == Decimal("19.50")


async def test_paid_call_blocked_when_global_hard_stop_exceeded(pool: DbPool) -> None:
    """Paid calls exceeding global hard-stop budget are refused."""
    await seed_connection(
        pool,
        "p_glob_budget",
        "c_glob_1",
        units={"credits": {"limit": 100, "unit_cost_usd": 0.10}},
    )
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    # Set global budget to $50.00 with hard stop
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
            "values ('global', null, 50.00, true)"
        )
        # Spend $49.50
        await conn.execute(
            "insert into public.billing_events (connection_id, kind, amount_usd, at) "
            "values ('c_glob_1', 'charge', 49.50, %s)",
            (now,),
        )

    decision = await budget.check(pool, "c_glob_1", Decimal("1.00"), now=now)
    assert decision.allowed is False
    assert decision.error_kind == "budget_exhausted"
    assert decision.scope == "global"
    assert decision.budget_usd == Decimal("50.00")
    assert decision.current_spend_usd == Decimal("49.50")


@respx.mock
async def test_soft_budget_allows_call_and_alerts_once_per_period(pool: DbPool) -> None:
    """Soft budgets (hard_stop=False) allow calls to proceed while firing threshold alerts."""
    clear_telegram_state()
    os.environ["TELEGRAM_BOT_TOKEN"] = "fake-token"
    os.environ["TELEGRAM_CHAT_ID"] = "12345678"

    respx.post("https://api.telegram.org/botfake-token/sendMessage").respond(
        status_code=200, json={"ok": True}
    )

    try:
        await seed_connection(
            pool,
            "p_soft",
            "c_soft",
            units={"credits": {"limit": 100, "unit_cost_usd": 0.10}},
        )
        now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

        # Soft budget: monthly $100, hard_stop = False
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
                "values ('connection', 'c_soft', 100.00, false)"
            )
            # Add $55 spend (55% -> triggers 50% alert)
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_soft', 'charge', 55.00, %s)",
                (now,),
            )

        # Call with est_cost = 5.00 -> total 60%
        # Because hard_stop is False, call is allowed!
        decision = await budget.check(pool, "c_soft", Decimal("5.00"), now=now)
        assert decision.allowed is True

        open_alerts = await list_open_alerts(pool)
        assert len(open_alerts) == 1
        assert "50%" in open_alerts[0]["message"]

        # Repeated call at the same spend does not re-alert
        decision2 = await budget.check(pool, "c_soft", Decimal("1.00"), now=now)
        assert decision2.allowed is True
        open_alerts_2 = await list_open_alerts(pool)
        assert len(open_alerts_2) == 1

        # Advance spend to 85% ($85)
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_soft', 'charge', 30.00, %s)",
                (now,),
            )

        decision3 = await budget.check(pool, "c_soft", Decimal("1.00"), now=now)
        assert decision3.allowed is True
        open_alerts_3 = await list_open_alerts(pool)
        assert len(open_alerts_3) == 2
        severities = {a["severity"] for a in open_alerts_3}
        assert severities == {"info", "warn"}

        # Advance spend to 105% ($105)
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_soft', 'charge', 20.00, %s)",
                (now,),
            )

        decision4 = await budget.check(pool, "c_soft", Decimal("1.00"), now=now)
        assert decision4.allowed is True  # Still allowed because hard_stop is False!
        open_alerts_4 = await list_open_alerts(pool)
        assert len(open_alerts_4) == 3
        severities_all = {a["severity"] for a in open_alerts_4}
        assert severities_all == {"info", "warn", "critical"}

    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


async def test_router_blocks_paid_call_when_hard_stop_exceeded(
    pool: DbPool, farm_factory: any, registry: Registry
) -> None:
    """The router refuses a paid connection when budget is exhausted, returning budget_exhausted."""
    # Build FarmContext with mock scripted executor
    executor = ScriptedExecutor(lambda req: ok_result())
    ctx: FarmContext = await farm_factory(
        registry,
        executors={"api": executor},
    )

    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    # Set unit_cost_usd to 0.01 so these are paid connections
    async with pool.connection() as conn:
        await conn.execute("update public.consumption_units set unit_cost_usd = 0.01")
        for conn_id in ("reoon-01", "reoon-02", "zerobounce-01"):
            await conn.execute(
                "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
                "values ('connection', %s, 10.00, true)",
                (conn_id,),
            )
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values (%s, 'charge', 10.00, %s)",
                (conn_id, now),
            )

    # Route call - all candidates are paid connections with est_cost > 0
    # and all have 0 hard-stop budgets
    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok is False
    assert out.error is not None
    assert out.error.kind == "budget_exhausted"
    assert "hard stop" in out.error.message.lower()
