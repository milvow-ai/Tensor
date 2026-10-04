"""Unit tests for M2 health features: circuit breaker, half-open probes, backoff, alerts, reactivation."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
import pytest_asyncio

from farm.db.pool import DbPool
from farm.executors.base import ErrorKind
from farm.resources import health
from tests.conftest import START, seed_connection
from tests.farm_helpers import fetch, status_of
from tests.farm_helpers import health as health_row

NOW = START

pytestmark = pytest.mark.usefixtures("account")


@pytest_asyncio.fixture
async def account(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "day"}})


async def fail(pool: DbPool, cid: str = "c", kind: ErrorKind = ErrorKind.SERVER, **kwargs: object) -> None:
    await health.record_failure(pool, cid, kind, "error", now=NOW, **kwargs)  # type: ignore[arg-type]


async def test_circuit_opens_with_custom_meta_threshold_and_open_s(pool: DbPool) -> None:
    meta = {"circuit": {"failures": 3, "open_seconds": 45}}
    await seed_connection(
        pool, "p", "c-custom", {"credits": {"limit": 10, "period": "day"}}, meta=json.dumps(meta)
    )

    await fail(pool, "c-custom", ErrorKind.SERVER)
    await fail(pool, "c-custom", ErrorKind.TIMEOUT)
    h = await health_row(pool, "c-custom")
    assert h["circuit"] == "closed" and h["consecutive_failures"] == 2

    # 3rd failure opens circuit
    await fail(pool, "c-custom", ErrorKind.SERVER)
    h = await health_row(pool, "c-custom")
    assert h["circuit"] == "open"
    assert h["consecutive_failures"] == 3
    assert h["cooldown_until"] == NOW + timedelta(seconds=45)


async def test_half_open_probe_claim_is_exclusive(pool: DbPool) -> None:
    for _ in range(5):
        await fail(pool, "c", ErrorKind.SERVER)
    h = await health_row(pool, "c")
    assert h["circuit"] == "open"

    probe_time = NOW + timedelta(seconds=125)

    # Concurrently attempt to claim probe
    results = await asyncio.gather(
        health.claim_probe(pool, "c", probe_time),
        health.claim_probe(pool, "c", probe_time),
    )
    # Exactly one claim succeeds
    assert sorted(results) == [False, True]

    h = await health_row(pool, "c")
    assert h["circuit"] == "half_open"
    assert h["cooldown_until"] is None
    # While half-open, other requests see circuit_open
    assert health.unavailable_reason("half_open", None, probe_time) == "circuit_open"


async def test_half_open_probe_success_closes_circuit(pool: DbPool) -> None:
    for _ in range(5):
        await fail(pool, "c", ErrorKind.SERVER)
    probe_time = NOW + timedelta(seconds=125)
    claimed = await health.claim_probe(pool, "c", probe_time)
    assert claimed

    # Probe succeeds
    await health.record_success(pool, "c", now=probe_time)
    h = await health_row(pool, "c")
    assert h["circuit"] == "closed"
    assert h["consecutive_failures"] == 0
    assert h["cooldown_until"] is None


async def test_half_open_probe_failure_with_backoff_doubles_open_time(pool: DbPool) -> None:
    meta = {"circuit": {"failures": 2, "open_seconds": 100, "backoff": True}}
    await seed_connection(
        pool, "p", "c-backoff", {"credits": {"limit": 10, "period": "day"}}, meta=json.dumps(meta)
    )

    t = NOW
    await fail(pool, "c-backoff", ErrorKind.SERVER)
    await fail(pool, "c-backoff", ErrorKind.SERVER)
    h = await health_row(pool, "c-backoff")
    assert h["circuit"] == "open"
    assert h["cooldown_until"] == t + timedelta(seconds=100)

    # First probe after window
    t = t + timedelta(seconds=105)
    assert await health.claim_probe(pool, "c-backoff", t)
    # Probe fails -> doubled to 200s
    await health.record_failure(pool, "c-backoff", ErrorKind.SERVER, "probe fail 1", now=t)
    h = await health_row(pool, "c-backoff")
    assert h["circuit"] == "open"
    assert h["cooldown_until"] == t + timedelta(seconds=200)

    # Second probe after 200s window
    t = t + timedelta(seconds=205)
    assert await health.claim_probe(pool, "c-backoff", t)
    # Probe fails -> doubled to 400s
    await health.record_failure(pool, "c-backoff", ErrorKind.SERVER, "probe fail 2", now=t)
    h = await health_row(pool, "c-backoff")
    assert h["circuit"] == "open"
    assert h["cooldown_until"] == t + timedelta(seconds=400)


async def test_auth_failure_creates_alert_and_needs_login(pool: DbPool) -> None:
    await fail(pool, "c", ErrorKind.AUTH)
    assert await status_of(pool, "c") == "needs_login"

    alerts = await fetch(pool, "select kind, severity, ref from public.alerts where ref = 'login:c'")
    assert len(alerts) == 1
    assert alerts[0] == ("needs_login", "critical", "login:c")

    # AUTH does not open circuit
    h = await health_row(pool, "c")
    assert h["circuit"] == "closed"
    assert h["consecutive_failures"] == 0


async def test_exhausted_connection_reactivates_due(pool: DbPool) -> None:
    reset_at = NOW + timedelta(minutes=30)
    await fail(pool, "c", ErrorKind.LIMIT_REACHED, reset_at=reset_at)
    assert await status_of(pool, "c") == "exhausted"

    # Before reset_at: reactivate_due does nothing
    reactivated = await health.reactivate_due(pool, NOW + timedelta(minutes=15))
    assert reactivated == 0
    assert await status_of(pool, "c") == "exhausted"

    # At or after reset_at: reactivate_due flips status to active
    reactivated = await health.reactivate_due(pool, NOW + timedelta(minutes=31))
    assert reactivated == 1
    assert await status_of(pool, "c") == "active"


async def test_rate_limited_jitter_bounds(pool: DbPool) -> None:
    await fail(pool, "c", ErrorKind.RATE_LIMITED, retry_after_s=None, jitter=True)
    h = await health_row(pool, "c")
    assert h["cooldown_until"] is not None
    delta = (h["cooldown_until"] - NOW).total_seconds()
    # Default 60s with uniform(0.8, 1.2) -> between 47.9 and 72.1
    assert 47.0 <= delta <= 73.0
