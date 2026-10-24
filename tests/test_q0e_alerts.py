"""Q0E: threshold alerts come once per month, the forecast has one formula, the renewal date has one rule.

Reproductions of the Q0 audit findings Q0-06, Q0-15 and Q0-16.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from farm.db.pool import DbPool
from farm.manager import alerts as alerts_module
from farm.manager.alerts import create_alert
from farm.manager.budgets import calculate_month_forecast, check_budget_thresholds
from farm.manager.renewals import check_renewal_reminders, compute_next_renewal_date
from tests.conftest import START, seed_connection
from tests.farm_helpers import fetch

NOW = START  # 2026-10-04 12:00 UTC


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records what would be sent to Telegram (the transport itself is outside this round)."""
    sent: list[str] = []

    async def record(text: str, dedup_key: str | None = None) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(alerts_module, "send_telegram_alert", record)
    return sent


# --- Q0-06 ------------------------------------------------------------------------------------------------------


async def _spend_six_of_ten(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 100})
    async with pool.connection() as conn:
        await conn.execute("insert into public.budgets (scope, ref, monthly_usd, hard_stop) values ('global', null, 10, false)")
        await conn.execute(
            "insert into public.usage_events (connection_id, unit, amount, kind, cost_usd, at) "
            "values ('c', 'credits', 1, 'actual', 6, %s)",
            (NOW,),
        )


async def test_an_acknowledged_threshold_alert_does_not_come_back_in_the_same_month(
    pool: DbPool, telegram: list[str]
) -> None:
    await _spend_six_of_ten(pool)
    await check_budget_thresholds(pool, NOW)
    async with pool.connection() as conn:
        await conn.execute("update public.alerts set acked_at = now()")

    await check_budget_thresholds(pool, NOW)  # runs on every paid router call and in the daily tick

    rows = await fetch(pool, "select count(*) from alerts where ref = 'budget:global:global:50:2026-10'")
    assert rows[0][0] == 1
    assert len(telegram) == 1, f"{len(telegram)} Telegram messages for one threshold in one month"


async def test_the_threshold_alert_returns_in_the_next_month(pool: DbPool, telegram: list[str]) -> None:
    await _spend_six_of_ten(pool)
    await check_budget_thresholds(pool, NOW)
    async with pool.connection() as conn:
        await conn.execute("update public.alerts set acked_at = now()")
        await conn.execute("update public.usage_events set at = %s", (NOW + timedelta(days=31),))

    await check_budget_thresholds(pool, NOW + timedelta(days=31))  # 2026-11-04

    refs = {r[0] for r in await fetch(pool, "select ref from alerts")}
    assert refs == {"budget:global:global:50:2026-10", "budget:global:global:50:2026-11"}


async def test_two_racing_callers_create_one_threshold_alert_and_one_message(
    pool: DbPool, telegram: list[str]
) -> None:
    ids = await asyncio.gather(
        *[create_alert(pool, "budget_threshold", "info", "50% reached", "budget:global:global:50:2026-10") for _ in range(8)]
    )
    assert len(set(ids)) == 1
    assert (await fetch(pool, "select count(*) from alerts"))[0][0] == 1
    assert len(telegram) == 1


async def test_other_alert_kinds_still_come_back_after_an_acknowledgement(pool: DbPool, telegram: list[str]) -> None:
    first = await create_alert(pool, "needs_login", "warn", "claude-02 needs login", "needs_login:claude-02")
    async with pool.connection() as conn:
        await conn.execute("update public.alerts set acked_at = now()")
    second = await create_alert(pool, "needs_login", "warn", "claude-02 needs login", "needs_login:claude-02")
    assert first != second  # an outage that returns after being acknowledged is news again


async def test_an_acknowledged_renewal_reminder_is_not_sent_again(pool: DbPool, telegram: list[str]) -> None:
    await seed_connection(pool, "p", "c", {"credits": 100}, plan={"name": "Pro", "price_usd": 20, "billing_day": 7})
    assert len(await check_renewal_reminders(pool, NOW)) == 1  # 3 days before the 7th
    async with pool.connection() as conn:
        await conn.execute("update public.alerts set acked_at = now()")

    await check_renewal_reminders(pool, NOW)

    assert (await fetch(pool, "select count(*) from alerts where kind = 'renewal_reminder'"))[0][0] == 1
    assert len(telegram) == 1


# --- Q0-15 ------------------------------------------------------------------------------------------------------


async def test_the_alert_forecast_and_the_console_forecast_give_the_same_number(pool: DbPool) -> None:
    at = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    in_python = calculate_month_forecast(100, at)
    # The elapsed-days expression of v_spend_month (console/sql/views.sql), evaluated at the same instant.
    rows = await fetch(
        pool,
        "select 100::numeric / greatest(extract(epoch from (%s::timestamptz - date_trunc('month', %s::timestamptz)))"
        " / 86400.0, 1.0)::numeric * 31",
        at,
        at,
    )
    in_sql = rows[0][0]
    assert abs(in_python - in_sql) < Decimal("0.01"), f"alert says ${in_python:.2f}, the Console says ${in_sql:.2f}"
    assert round(in_python, 2) == Decimal("213.79")


def test_the_forecast_floors_the_first_day_at_one_full_day() -> None:
    assert calculate_month_forecast(10, datetime(2026, 10, 1, 6, 0, tzinfo=UTC)) == Decimal(10) * 31


# --- Q0-16 ------------------------------------------------------------------------------------------------------

# (billing_day, today, expected next renewal): clamp to the month's last day, never roll into the next month
RENEWAL_CASES = [
    (31, date(2026, 2, 10), date(2026, 2, 28)),
    (31, date(2026, 3, 5), date(2026, 3, 31)),
    (31, date(2026, 3, 31), date(2026, 3, 31)),
    (31, date(2026, 4, 1), date(2026, 4, 30)),
    (31, date(2026, 4, 30), date(2026, 4, 30)),
    (31, date(2026, 5, 1), date(2026, 5, 31)),
    (30, date(2026, 2, 28), date(2026, 2, 28)),
    (30, date(2026, 6, 10), date(2026, 6, 30)),
    (29, date(2026, 2, 3), date(2026, 2, 28)),
    (29, date(2028, 2, 3), date(2028, 2, 29)),
    (29, date(2026, 3, 30), date(2026, 4, 29)),
    (31, date(2026, 12, 31), date(2026, 12, 31)),
    (31, date(2027, 1, 1), date(2027, 1, 31)),
    (15, date(2026, 12, 20), date(2027, 1, 15)),
]


@pytest.mark.parametrize(("billing_day", "today", "expected"), RENEWAL_CASES)
async def test_the_reminder_job_and_the_console_view_use_one_renewal_rule(
    pool: DbPool, billing_day: int, today: date, expected: date
) -> None:
    at = datetime(today.year, today.month, today.day, 9, 30, tzinfo=UTC)
    in_python = compute_next_renewal_date({"billing_day": billing_day}, at)
    in_sql = (await fetch(pool, "select farm_next_renewal(%s, %s)", billing_day, at))[0][0]
    assert in_python == expected
    assert in_sql == expected, f"v_renewals would show {in_sql}, the reminder job uses {in_python}"


@pytest.mark.parametrize("billing_day", [29, 30, 31])
async def test_v_renewals_shows_the_date_the_reminder_job_uses(pool: DbPool, billing_day: int) -> None:
    await seed_connection(
        pool, "p", "c", {"credits": 100}, plan={"name": "Pro", "price_usd": 20, "billing_day": billing_day}
    )
    expected = compute_next_renewal_date({"billing_day": billing_day}, datetime.now(UTC))
    assert expected is not None
    if expected == datetime.now(UTC).date():
        pytest.skip("the renewal is today: the view lists only renewals that are still ahead")
    rows = await fetch(pool, "select (renews_on at time zone 'UTC')::date from v_renewals where connection_id = 'c'")
    assert rows == [(expected,)]
