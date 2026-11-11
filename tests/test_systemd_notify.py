"""systemd readiness begins after HTTP startup and stop is signaled on cancellation."""
import asyncio

import pytest

from farm.control import systemd


async def test_supervisor_waits_for_http_then_notifies_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", "/test/unused")
    sent: list[str] = []
    monkeypatch.setattr(systemd, "notify", lambda value: sent.append(value))
    started = False
    task = asyncio.create_task(systemd.supervise(lambda: started, lambda: False))
    await asyncio.sleep(0.02)
    assert not sent
    started = True
    await asyncio.sleep(0.15)
    assert sent == ["READY=1", "WATCHDOG=1"]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert sent[-1] == "STOPPING=1"


async def test_non_systemd_process_never_sends_a_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    monkeypatch.setattr(systemd, "notify", lambda _: pytest.fail("unexpected notification"))
    await systemd.supervise(lambda: True, lambda: False)
