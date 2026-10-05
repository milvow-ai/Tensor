"""Tests for farm/control/alerts.py: alert creation, 1-hour deduplication, Telegram dispatch, and CLI."""

import os
from datetime import UTC, datetime, timedelta

import respx
from typer.testing import CliRunner

from farm.control.alerts import ALERT_KINDS, ack_alert, list_alerts, send_alert
from farm.control.cli import app
from farm.db.pool import DbPool
from farm.manager.telegram import clear_telegram_state

runner = CliRunner()


async def test_send_alert_persists_in_db(pool: DbPool) -> None:
    aid = await send_alert(
        pool,
        kind="farm_down",
        message="Farm service failed health check",
        severity="critical",
        ref="watchdog:farm_down:1",
        notify_telegram=False,
    )
    assert aid is not None

    alerts = await list_alerts(pool)
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "farm_down"
    assert alerts[0]["severity"] == "critical"
    assert "health check" in alerts[0]["message"]
    assert alerts[0]["ref"] == "watchdog:farm_down:1"


async def test_alert_deduplication_1h(pool: DbPool) -> None:
    now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)

    aid1 = await send_alert(
        pool,
        kind="needs_login",
        message="Account claude-02 credentials expired",
        severity="critical",
        ref="login:claude-02",
        notify_telegram=False,
        now=now,
    )

    # Identical alert 30 minutes later should be deduplicated
    aid2 = await send_alert(
        pool,
        kind="needs_login",
        message="Account claude-02 credentials expired",
        severity="critical",
        ref="login:claude-02",
        notify_telegram=False,
        now=now + timedelta(minutes=30),
    )
    assert aid1 == aid2

    alerts = await list_alerts(pool)
    assert len(alerts) == 1

    # Identical alert after 65 minutes (> 1 hour) creates a new alert
    aid3 = await send_alert(
        pool,
        kind="needs_login",
        message="Account claude-02 credentials expired",
        severity="critical",
        ref="login:claude-02",
        notify_telegram=False,
        now=now + timedelta(minutes=65),
    )
    assert aid3 != aid1

    alerts_after = await list_alerts(pool)
    assert len(alerts_after) == 2


@respx.mock
async def test_alert_telegram_notification(pool: DbPool) -> None:
    clear_telegram_state()
    os.environ["TELEGRAM_BOT_TOKEN"] = "test-bot-token"
    os.environ["TELEGRAM_CHAT_ID"] = "987654321"

    tg_route = respx.post("https://api.telegram.org/bottest-bot-token/sendMessage").respond(
        status_code=200, json={"ok": True}
    )

    aid = await send_alert(
        pool,
        kind="account_exhausted",
        message="Connection codex-01 reached quota limit",
        severity="warn",
        ref="exhausted:codex-01",
        notify_telegram=True,
    )
    assert aid is not None
    assert tg_route.called
    req_body = tg_route.calls.last.request.content.decode("utf-8")
    assert "987654321" in req_body
    assert "account_exhausted" in req_body


async def test_alert_kinds_supported(pool: DbPool) -> None:
    expected_kinds = {
        "farm_down",
        "needs_login",
        "account_exhausted",
        "circuit_open_long",
        "budget_threshold",
    }
    assert expected_kinds.issubset(ALERT_KINDS)

    for k in expected_kinds:
        aid = await send_alert(
            pool,
            kind=k,
            message=f"Test alert for {k}",
            severity="warn",
            notify_telegram=False,
        )
        assert aid is not None


async def test_ack_alert(pool: DbPool) -> None:
    aid = await send_alert(
        pool,
        kind="circuit_open_long",
        message="Circuit open > 15m for provider reoon",
        severity="warn",
        notify_telegram=False,
    )
    open_alerts = await list_alerts(pool, only_unacked=True)
    assert len(open_alerts) == 1

    acked = await ack_alert(pool, aid)
    assert acked is True

    open_after = await list_alerts(pool, only_unacked=True)
    assert len(open_after) == 0

    all_alerts = await list_alerts(pool, only_unacked=False)
    assert len(all_alerts) == 1
    assert all_alerts[0]["acked_at"] is not None


@respx.mock
def test_cli_alert_send(monkeypatch: any) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = runner.invoke(
        app,
        [
            "alert",
            "send",
            "--kind",
            "farm_down",
            "--message",
            "CLI test farm down alert",
            "--severity",
            "critical",
        ],
    )
    assert result.exit_code == 0
    assert "alert sent:" in result.stdout
