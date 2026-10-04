"""Tests for farm/manager/balance.py: snapshot recording, API balance sync, and drift alerts."""

from decimal import Decimal
from unittest.mock import AsyncMock

from farm.adapters._template import BalanceResult
from farm.db.pool import DbPool
from farm.manager.alerts import list_open_alerts
from farm.manager.balance import (
    record_balance_snapshot,
    sync_connection_balance,
)
from tests.conftest import seed_connection


async def test_manual_balance_snapshot_recorded(pool: DbPool) -> None:
    await seed_connection(pool, "p_bal", "c_bal_1", {"credits": 500})

    snap_id = await record_balance_snapshot(
        pool,
        connection_id="c_bal_1",
        unit="credits",
        remaining=450,
        source="manual",
    )
    assert snap_id is not None

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select remaining, source from public.balance_snapshots where id = %s",
            (snap_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert Decimal(str(row[0])) == Decimal("450")
        assert row[1] == "manual"


async def test_api_balance_sync_records_snapshot(pool: DbPool) -> None:
    await seed_connection(pool, "reoon", "reoon-test-01", {"credits": 1000}, auth_ref="env:REOON_KEY")

    mock_adapter = AsyncMock()
    mock_adapter.provider_id = "reoon"
    mock_adapter.balance_unit = "credits"
    mock_adapter.balance.return_value = BalanceResult(
        ok=True,
        remaining=950.0,
        units={"credits": 950.0, "daily_credits": 450.0, "instant_credits": 500.0},
    )

    snapshots = await sync_connection_balance(
        pool,
        "reoon-test-01",
        adapter=mock_adapter,
        check_drift=False,
    )
    assert len(snapshots) == 3

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select unit, remaining, source from public.balance_snapshots "
            "where connection_id = 'reoon-test-01' order by unit"
        )
        rows = await cur.fetchall()
        assert len(rows) == 3
        units = {r[0]: (float(r[1]), r[2]) for r in rows}
        assert units["credits"] == (950.0, "api")
        assert units["daily_credits"] == (450.0, "api")
        assert units["instant_credits"] == (500.0, "api")


async def test_balance_drift_above_10_percent_raises_alert(pool: DbPool) -> None:
    """Ledger estimated 500, but provider snapshot is 300 (drift = 200 / 300 = 66.7% > 10%) -> alert."""
    await seed_connection(pool, "p_bal", "c_drift_1", {"credits": 500})

    # Record snapshot with 300 (drift from 500 is > 10%)
    await record_balance_snapshot(
        pool,
        connection_id="c_drift_1",
        unit="credits",
        remaining=300,
        source="api",
        check_drift=True,
    )

    alerts = await list_open_alerts(pool)
    drift_alerts = [a for a in alerts if a["kind"] == "balance_drift"]
    assert len(drift_alerts) == 1
    assert "Balance drift" in drift_alerts[0]["message"]
    assert "c_drift_1" in drift_alerts[0]["message"]


async def test_balance_drift_within_10_percent_does_not_raise_alert(pool: DbPool) -> None:
    """Ledger estimated 500, snapshot is 480 (drift = 20 / 480 = 4.1% <= 10%) -> no alert."""
    await seed_connection(pool, "p_bal", "c_drift_2", {"credits": 500})

    await record_balance_snapshot(
        pool,
        connection_id="c_drift_2",
        unit="credits",
        remaining=480,
        source="manual",
        check_drift=True,
    )

    alerts = await list_open_alerts(pool)
    drift_alerts = [a for a in alerts if a["kind"] == "balance_drift"]
    assert len(drift_alerts) == 0
