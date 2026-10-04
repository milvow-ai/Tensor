"""Unit tests for process heartbeat recording, checking, and keep-awake state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from farm.control.doctor import CheckResult
from farm.control.heartbeat import (
    HeartbeatInfo,
    HeartbeatWriter,
    check_heartbeat,
    read_heartbeat,
    record_heartbeat,
)
from farm.control.keepawake import (
    KeepAwake,
    check_keepawake,
    disable_keepawake,
    enable_keepawake,
    is_keepawake_active,
)


def test_record_and_read_heartbeat(tmp_path: Path) -> None:
    """Record a heartbeat with metadata and read it back from disk."""
    info = record_heartbeat(
        component="test_worker",
        data_directory=tmp_path,
        extra={"custom_key": 123, "active": True},
    )
    assert info.component == "test_worker"
    assert info.pid > 0
    assert info.extra == {"custom_key": 123, "active": True}

    read_info = read_heartbeat(component="test_worker", data_directory=tmp_path)
    assert read_info is not None
    assert read_info.component == "test_worker"
    assert read_info.pid == info.pid
    assert read_info.extra == {"custom_key": 123, "active": True}
    assert abs((read_info.timestamp - info.timestamp).total_seconds()) < 1.0


def test_check_heartbeat_fresh(tmp_path: Path) -> None:
    """A recent heartbeat should return PASS."""
    record_heartbeat(component="command_consumer", data_directory=tmp_path)
    result = check_heartbeat("command_consumer", max_age_s=60.0, data_directory=tmp_path)
    assert isinstance(result, CheckResult)
    assert result.status == "PASS"
    assert "command_consumer" in result.name
    assert "PID" in result.detail


def test_check_heartbeat_stale(tmp_path: Path) -> None:
    """A heartbeat older than max_age_s must return FAIL."""
    # Write a heartbeat 90 seconds ago
    stale_time = datetime.now(UTC) - timedelta(seconds=90)
    info = HeartbeatInfo(
        component="command_consumer",
        timestamp=stale_time,
        pid=1234,
        extra={},
    )
    hb_dir = tmp_path / "heartbeats"
    hb_dir.mkdir(parents=True, exist_ok=True)
    import json

    with open(hb_dir / "command_consumer.json", "w", encoding="utf-8") as f:
        json.dump(info.to_dict(), f)

    result = check_heartbeat("command_consumer", max_age_s=60.0, data_directory=tmp_path)
    assert result.status == "FAIL"
    assert "stale" in result.detail
    assert result.fix_hint != ""


def test_check_heartbeat_missing(tmp_path: Path) -> None:
    """Missing heartbeat file must return FAIL with fix hint."""
    result = check_heartbeat("nonexistent_service", max_age_s=60.0, data_directory=tmp_path)
    assert result.status == "FAIL"
    assert "No heartbeat found" in result.detail
    assert result.fix_hint != ""


@pytest.mark.asyncio
async def test_heartbeat_writer_lifecycle(tmp_path: Path) -> None:
    """HeartbeatWriter writes periodic heartbeats and stops cleanly."""
    writer = HeartbeatWriter(
        component="writer_test",
        interval_s=0.05,
        data_directory=tmp_path,
        extra_fn=lambda: {"mode": "testing"},
    )
    await writer.start()
    try:
        await asyncio.sleep(0.12)
        info = read_heartbeat("writer_test", data_directory=tmp_path)
        assert info is not None
        assert info.extra.get("mode") == "testing"
    finally:
        await writer.stop()


def test_keepawake_state_lifecycle(tmp_path: Path) -> None:
    """KeepAwake enabling, checking, and context manager in temporary directory."""
    try:
        enable_keepawake(away_mode=True, data_directory=tmp_path)
        assert is_keepawake_active(data_directory=tmp_path) is True

        res = check_keepawake(data_directory=tmp_path)
        assert res.status == "PASS"
        assert "active" in res.detail

        disable_keepawake(data_directory=tmp_path)
        assert is_keepawake_active(data_directory=tmp_path) is False

        res2 = check_keepawake(data_directory=tmp_path)
        assert res2.status == "WARN"

        # Context manager
        with KeepAwake(away_mode=False, data_directory=tmp_path):
            assert is_keepawake_active(data_directory=tmp_path) is True
        assert is_keepawake_active(data_directory=tmp_path) is False
    finally:
        disable_keepawake(data_directory=tmp_path)
