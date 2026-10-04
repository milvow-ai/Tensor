"""Tests for farm/manager/budgets.py: spend tracking, forecasting, threshold alerts, and hard-stop policy."""

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import respx

from farm.control.commands import process_command
from farm.db.pool import DbPool
from farm.manager.alerts import list_open_alerts
from farm.manager.budgets import (
    calculate_month_forecast,
    check_budget_thresholds,
    check_paid_call,
    get_month_to_date_spend,
    record_policy_block,
)
from farm.manager.telegram import clear_telegram_state
from tests.conftest import seed_connection


async def test_month_to_date_spend_combines_usage_and_billing(pool: DbPool) -> None:
    await seed_connection(pool, "p_spend", "c_spend_1", {"credits": 100})
    now = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

    async with pool.connection() as conn:
        # Usage event: $5.50
        await conn.execute(
            "insert into public.usage_events (connection_id, unit, amount, kind, cost_usd, at) "
            "values ('c_spend_1', 'credits', 10, 'actual', 5.50, %s)",
            (now,),
        )
        # Billing event: charge $15.00
        await conn.execute(
            "insert into public.billing_events (connection_id, kind, amount_usd, at) "
            "values ('c_spend_1', 'charge', 15.00, %s)",
            (now,),
        )
        # Billing event: refund $2.00
        await conn.execute(
            "insert into public.billing_events (connection_id, kind, amount_usd, at) "
            "values ('c_spend_1', 'refund', 2.00, %s)",
            (now,),
        )

    spend = await get_month_to_date_spend(pool, "connection", "c_spend_1", now=now)
    # 5.50 + 15.00 - 2.00 = 18.50
    assert spend == Decimal("18.50")


def test_calculate_month_forecast() -> None:
    now = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)  # 15 days elapsed in a 31-day month
    # Spent $150 in 15 days -> forecast is $310
    forecast = calculate_month_forecast(Decimal("150"), now=now)
    assert round(forecast, 2) == Decimal("310.00")


async def test_budget_hard_stop_blocks_paid_and_allows_free(pool: DbPool) -> None:
    """Acceptance test: global budget 0, paid connection blocked, free one allowed.

    Raise budget via set_budget command -> paid becomes eligible.
    """
    # Seed free connection (unit_cost_usd = 0)
    await seed_connection(
        pool,
        "p_free",
        "c_free",
        units={"credits": {"limit": 100, "unit_cost_usd": 0}},
    )
    # Seed paid connection (unit_cost_usd = 0.01)
    await seed_connection(
        pool,
        "p_paid",
        "c_paid",
        units={"credits": {"limit": 100, "unit_cost_usd": 0.01}},
    )

    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    # Set global budget to 0 with hard stop
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.budgets (scope, ref, monthly_usd, hard_stop) values ('global', null, 0, true)"
        )

    # Free call (est_cost = 0) is allowed
    free_decision = await check_paid_call(pool, "c_free", Decimal("0.00"), now=now)
    assert free_decision.allowed is True
    assert bool(free_decision) is True

    # Paid call (est_cost = 0.01) is blocked by hard stop
    paid_decision = await check_paid_call(pool, "c_paid", Decimal("0.01"), now=now)
    assert paid_decision.allowed is False
    assert bool(paid_decision) is False
    assert paid_decision.error_kind == "budget_exhausted"
    assert "hard stop" in (paid_decision.reason or "").lower()

    # Log policy_block event
    run_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.runs (id, capability, caller, status) values (%s, 'verify_email', 'test', 'blocked')",
            (run_id,),
        )
    await record_policy_block(pool, run_id, "c_paid", paid_decision.reason or "budget exhausted")

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select kind, connection_id from public.run_events where run_id = %s",
            (run_id,),
        )
        ev = await cur.fetchone()
        assert ev is not None
        assert ev[0] == "policy_block"
        assert ev[1] == "c_paid"

    # Raise budget via a set_budget command to $50.00
    cmd_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) "
            "values (%s, 'set_budget', '{\"scope\": \"global\", \"monthly_usd\": 50.0, \"hard_stop\": true}'::jsonb, 'queued')",
            (cmd_id,),
        )

    status, result = await process_command(pool, cmd_id)
    assert status == "done"

    # Now paid call becomes eligible
    paid_decision_after = await check_paid_call(pool, "c_paid", Decimal("0.01"), now=now)
    assert paid_decision_after.allowed is True


@respx.mock
async def test_threshold_alerts_and_telegram_notifications(pool: DbPool) -> None:
    """Acceptance test: spend crossing 50/80/100% creates exactly one alert each per month;

    Telegram mock called once per alert.
    """
    clear_telegram_state()
    os.environ["TELEGRAM_BOT_TOKEN"] = "fake-token"
    os.environ["TELEGRAM_CHAT_ID"] = "12345678"

    # Mock Telegram sendMessage endpoint
    tg_route = respx.post("https://api.telegram.org/botfake-token/sendMessage").respond(
        status_code=200, json={"ok": True}
    )

    try:
        await seed_connection(pool, "p_thresh", "c_thresh_1", {"credits": 1000})
        now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

        # Budget of $100
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.budgets (scope, ref, monthly_usd, hard_stop) values ('connection', 'c_thresh_1', 100, true)"
            )
            # Add $55 spend -> crosses 50%
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_thresh_1', 'charge', 55.00, %s)",
                (now,),
            )

        alerts_1 = await check_budget_thresholds(pool, now=now)
        # Exactly one alert for 50%
        assert len(alerts_1) == 1
        assert tg_route.call_count == 1

        # Running again does NOT duplicate the alert
        alerts_repeat = await check_budget_thresholds(pool, now=now)
        assert len(alerts_repeat) == 1  # returns the existing deduplicated ID
        assert tg_route.call_count == 1  # Telegram not called again

        # Add more spend to cross 85% ($85 total)
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_thresh_1', 'charge', 30.00, %s)",
                (now,),
            )

        alerts_2 = await check_budget_thresholds(pool, now=now)
        # Should now have 2 alerts total (50% and 80%)
        assert len(alerts_2) == 2
        assert tg_route.call_count == 2

        # Add more spend to cross 100% ($105 total)
        async with pool.connection() as conn:
            await conn.execute(
                "insert into public.billing_events (connection_id, kind, amount_usd, at) "
                "values ('c_thresh_1', 'charge', 20.00, %s)",
                (now,),
            )

        alerts_3 = await check_budget_thresholds(pool, now=now)
        # Should now have 3 alerts total (50%, 80%, 100%)
        assert len(alerts_3) == 3
        assert tg_route.call_count == 3

        open_alerts = await list_open_alerts(pool)
        assert len(open_alerts) == 3
        thresh_severities = {a["severity"] for a in open_alerts}
        assert thresh_severities == {"info", "warn", "critical"}

    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)
