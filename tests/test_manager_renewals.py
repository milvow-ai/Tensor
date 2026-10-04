"""Tests for farm/manager/renewals.py: time-travel renewal reminders and human task alerts."""

from datetime import UTC, datetime

from farm.db.pool import DbPool
from farm.manager.alerts import list_open_alerts
from farm.manager.renewals import (
    check_renewal_reminders,
    request_human_task,
)
from tests.conftest import seed_connection


async def test_renewal_reminder_fires_3_days_before_billing_day(pool: DbPool) -> None:
    """Acceptance test: reminder fires in a time-travel test 3 days before billing_day,

    with usage %; not twice.
    """
    # Seed connection with billing_day = 14, plan name 'launch', price 185
    # Unit limit = 1000 credits
    await seed_connection(
        pool,
        "p_ren",
        "c_ren_1",
        units={"credits": {"limit": 1000, "period": "month", "anchor": 14}},
        plan={"name": "launch", "price_usd": 185, "billing_day": 14},
    )

    # Time-travel: current date is Oct 11, 2026 -> exactly 3 days before Oct 14 renewal
    current_time = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)

    # Record some usage in the current period (e.g. 750 credits used = 75% usage)
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.quota_usage (connection_id, unit, period_start, used, reserved, limit_value) "
            "values ('c_ren_1', 'credits', '2026-09-14 00:00+00', 750, 0, 1000)"
        )

    # Run reminder check at Oct 11 (3 days before)
    alerts = await check_renewal_reminders(pool, now=current_time)
    assert len(alerts) == 1

    open_alerts = await list_open_alerts(pool)
    assert len(open_alerts) == 1
    msg = open_alerts[0]["message"]
    assert "75.0%" in msg
    assert "launch" in msg
    assert "c_ren_1" in msg
    assert "2026-10-14" in msg
    assert "keep" in msg  # high usage suggestion

    # Run again at Oct 11 -> must not fire twice!
    alerts_again = await check_renewal_reminders(pool, now=current_time)
    assert len(alerts_again) == 0
    assert len(await list_open_alerts(pool)) == 1

    # Time-travel: Oct 12 (2 days before) -> should not create another reminder for this cycle
    next_day = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
    alerts_next_day = await check_renewal_reminders(pool, now=next_day)
    assert len(alerts_next_day) == 0
    assert len(await list_open_alerts(pool)) == 1


async def test_renewal_reminder_does_not_fire_when_not_3_days_before(pool: DbPool) -> None:
    await seed_connection(
        pool,
        "p_ren",
        "c_ren_2",
        plan={"name": "pro", "billing_day": 20},
    )
    # Today is Oct 10 (10 days before Oct 20) -> should not fire
    today = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    alerts = await check_renewal_reminders(pool, now=today)
    assert len(alerts) == 0


async def test_human_task_alert_creates_alert_with_billing_url(pool: DbPool) -> None:
    await seed_connection(
        pool,
        "clay",
        "clay-01",
        meta={"billing_url": "https://app.clay.com/settings/billing"},
    )

    alert_id = await request_human_task(
        pool,
        "clay-01",
        action="upgrade",
        note="Approaching credit limit on workspace 1",
    )
    assert alert_id is not None

    open_alerts = await list_open_alerts(pool)
    human_tasks = [a for a in open_alerts if a["kind"] == "human_task"]
    assert len(human_tasks) == 1
    assert "UPGRADE" in human_tasks[0]["message"]
    assert "https://app.clay.com/settings/billing" in human_tasks[0]["message"]
    assert "Approaching credit limit" in human_tasks[0]["message"]
