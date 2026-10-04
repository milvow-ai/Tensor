"""Tests for farm/manager/alerts.py and farm/manager/telegram.py."""

import os

import respx

from farm.db.pool import DbPool
from farm.manager.alerts import ack_alert, create_alert, list_open_alerts
from farm.manager.telegram import (
    clear_telegram_state,
    is_telegram_configured,
    send_telegram_alert,
)


async def test_create_and_ack_alert(pool: DbPool) -> None:
    aid = await create_alert(
        pool,
        kind="system_warning",
        severity="warn",
        message="System load is high",
        ref="load:cpu",
        notify_telegram=False,
    )
    assert aid is not None

    open_alerts = await list_open_alerts(pool)
    assert len(open_alerts) == 1
    assert open_alerts[0]["id"] == aid
    assert open_alerts[0]["kind"] == "system_warning"
    assert open_alerts[0]["severity"] == "warn"

    # Acknowledge the alert
    acked = await ack_alert(pool, aid)
    assert acked is True

    # No longer in open alerts
    assert len(await list_open_alerts(pool)) == 0

    # Acking already acknowledged alert returns False
    assert await ack_alert(pool, aid) is False


async def test_alert_deduplication(pool: DbPool) -> None:
    aid1 = await create_alert(
        pool,
        kind="login_needed",
        severity="critical",
        message="Please log in to Clay-01",
        ref="login:clay-01",
        notify_telegram=False,
        deduplicate=True,
    )
    aid2 = await create_alert(
        pool,
        kind="login_needed",
        severity="critical",
        message="Please log in to Clay-01",
        ref="login:clay-01",
        notify_telegram=False,
        deduplicate=True,
    )
    assert aid1 == aid2

    open_alerts = await list_open_alerts(pool)
    assert len(open_alerts) == 1


@respx.mock
async def test_telegram_alert_dispatch_and_rate_limiting() -> None:
    clear_telegram_state()
    os.environ["TELEGRAM_BOT_TOKEN"] = "token123"
    os.environ["TELEGRAM_CHAT_ID"] = "chat456"

    route = respx.post("https://api.telegram.org/bottoken123/sendMessage").respond(
        status_code=200, json={"ok": True}
    )

    try:
        assert is_telegram_configured() is True

        # Send first message
        res1 = await send_telegram_alert("Alert 1", dedup_key="key1")
        assert res1 is True
        assert route.call_count == 1

        # Duplicate message with same key within window is skipped
        res2 = await send_telegram_alert("Alert 1", dedup_key="key1")
        assert res2 is False
        assert route.call_count == 1

        # Message with different key is sent
        res3 = await send_telegram_alert("Alert 2", dedup_key="key2")
        assert res3 is True
        assert route.call_count == 2

    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        os.environ.pop("TELEGRAM_CHAT_ID", None)


async def test_telegram_skipped_when_not_configured() -> None:
    clear_telegram_state()
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    os.environ.pop("TELEGRAM_CHAT_ID", None)

    assert is_telegram_configured() is False
    res = await send_telegram_alert("Test message")
    assert res is False
