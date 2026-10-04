"""Connection health (M1 subset), tested directly: every outcome's effect on counters, circuit, cooldown and status."""

from __future__ import annotations

import asyncio
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


async def fail(pool: DbPool, kind: ErrorKind, **kwargs: object) -> None:
    await health.record_failure(pool, "c", kind, "boom", now=NOW, **kwargs)  # type: ignore[arg-type]


async def test_success_counts_and_clears_everything_transient(pool: DbPool) -> None:
    for _ in range(3):
        await fail(pool, ErrorKind.SERVER)
    await health.record_success(pool, "c", now=NOW)

    h = await health_row(pool, "c")
    assert (h["success_count"], h["failure_count"], h["consecutive_failures"], h["circuit"]) == (
        1,
        3,
        0,
        "closed",
    )


@pytest.mark.parametrize("kind", [ErrorKind.EMPTY, ErrorKind.BAD_REQUEST])
async def test_requests_the_provider_answered_correctly_leave_no_trace(pool: DbPool, kind: ErrorKind) -> None:
    await fail(pool, kind)
    assert await fetch(pool, "select 1 from connection_health") == []
    assert await status_of(pool, "c") == "active"


@pytest.mark.parametrize("kind", [ErrorKind.SERVER, ErrorKind.TIMEOUT, ErrorKind.UNKNOWN])
async def test_only_server_timeout_and_unknown_count_toward_the_circuit(
    pool: DbPool, kind: ErrorKind
) -> None:
    for _ in range(4):
        await fail(pool, kind)
    assert (await health_row(pool, "c"))["circuit"] == "closed"

    await fail(pool, kind)

    h = await health_row(pool, "c")
    assert (h["circuit"], h["consecutive_failures"], h["last_error_kind"]) == ("open", 5, kind.value)
    assert h["cooldown_until"] == NOW + timedelta(seconds=120)


@pytest.mark.parametrize("kind", [ErrorKind.RATE_LIMITED, ErrorKind.AUTH, ErrorKind.LIMIT_REACHED])
async def test_account_level_failures_never_open_the_circuit(pool: DbPool, kind: ErrorKind) -> None:
    for _ in range(8):
        await fail(pool, kind)
    h = await health_row(pool, "c")
    assert (h["circuit"], h["consecutive_failures"], h["failure_count"]) == ("closed", 0, 8)


async def test_rate_limit_cooldown_uses_retry_after_defaults_to_a_minute_and_is_capped(pool: DbPool) -> None:
    await fail(pool, ErrorKind.RATE_LIMITED, retry_after_s=45)
    assert (await health_row(pool, "c"))["cooldown_until"] == NOW + timedelta(seconds=45)

    await fail(pool, ErrorKind.RATE_LIMITED)  # a shorter default must not shorten a running cooldown
    assert (await health_row(pool, "c"))["cooldown_until"] == NOW + timedelta(seconds=60)

    await fail(pool, ErrorKind.RATE_LIMITED, retry_after_s=10)
    assert (await health_row(pool, "c"))["cooldown_until"] == NOW + timedelta(seconds=60)  # never shortened

    await fail(pool, ErrorKind.RATE_LIMITED, retry_after_s=10**9)  # a hostile Retry-After
    assert (await health_row(pool, "c"))["cooldown_until"] == NOW + timedelta(hours=24)


async def test_a_late_success_does_not_lift_a_running_cooldown_or_open_circuit(pool: DbPool) -> None:
    for _ in range(5):
        await fail(pool, ErrorKind.SERVER)  # circuit open until NOW+120s
    await health.record_success(
        pool, "c", now=NOW + timedelta(seconds=10)
    )  # a call that started before the trouble

    h = await health_row(pool, "c")
    assert (h["circuit"], h["consecutive_failures"], h["cooldown_until"]) == (
        "open",
        5,
        NOW + timedelta(seconds=120),
    )
    assert h["success_count"] == 1

    await health.record_success(pool, "c", now=NOW + timedelta(seconds=121))  # the probe after the window
    h = await health_row(pool, "c")
    assert (h["circuit"], h["consecutive_failures"], h["cooldown_until"]) == ("closed", 0, None)


async def test_auth_failure_needs_login_but_never_overrides_the_owners_pause(pool: DbPool) -> None:
    await fail(pool, ErrorKind.AUTH)
    assert await status_of(pool, "c") == "needs_login"

    await fetch(pool, "update connections set status = 'paused' where id = 'c' returning 1")
    await fail(pool, ErrorKind.AUTH)
    await fail(pool, ErrorKind.LIMIT_REACHED)
    assert await status_of(pool, "c") == "paused"


async def test_limit_reached_exhausts_until_the_providers_reset_time(pool: DbPool) -> None:
    reset = NOW + timedelta(hours=7)
    await fail(pool, ErrorKind.LIMIT_REACHED, reset_at=reset)

    assert await status_of(pool, "c") == "exhausted"
    assert (await health_row(pool, "c"))["cooldown_until"] == reset


async def test_limit_reached_without_a_reset_time_waits_for_the_next_period_of_the_exhausted_unit(
    pool: DbPool,
) -> None:
    await seed_connection(
        pool, "p", "c2", {"a": {"limit": 5, "period": "hour"}, "b": {"limit": 5, "period": "day"}}
    )
    await fetch(
        pool,
        "insert into quota_usage (connection_id, unit, period_start, used, reserved, limit_value) "
        "values ('c2', 'b', farm_period_start('day', null, %s), 5, 0, 5) returning 1",
        NOW,
    )  # the daily unit is spent, the hourly one is not

    await health.record_failure(pool, "c2", ErrorKind.LIMIT_REACHED, "out", now=NOW)

    until = (await health_row(pool, "c2"))["cooldown_until"]
    assert until == NOW.replace(hour=0, minute=0) + timedelta(days=1)  # tomorrow, not the next hour


async def test_limit_reached_with_nothing_to_go_by_probes_again_in_an_hour(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c3", {"credits": {"limit": None, "period": "none"}})

    await health.record_failure(pool, "c3", ErrorKind.LIMIT_REACHED, "out", now=NOW)

    assert await status_of(pool, "c3") == "exhausted"
    assert (await health_row(pool, "c3"))["cooldown_until"] == NOW + timedelta(hours=1)


async def test_success_revives_an_exhausted_account_only(pool: DbPool) -> None:
    await fail(pool, ErrorKind.LIMIT_REACHED, reset_at=NOW + timedelta(hours=1))
    await health.record_success(pool, "c", now=NOW + timedelta(hours=2))
    assert await status_of(pool, "c") == "active"

    await fetch(pool, "update connections set status = 'needs_login' where id = 'c' returning 1")
    await health.record_success(pool, "c", now=NOW + timedelta(hours=3))
    assert await status_of(pool, "c") == "needs_login"  # only a person clears a login problem


async def test_200_concurrent_failures_are_counted_exactly(pool: DbPool) -> None:
    await asyncio.gather(*(fail(pool, ErrorKind.SERVER) for _ in range(200)))

    h = await health_row(pool, "c")
    assert (h["failure_count"], h["consecutive_failures"], h["circuit"]) == (200, 200, "open")
    assert h["cooldown_until"] == NOW + timedelta(seconds=120)


async def test_concurrent_failures_and_successes_never_corrupt_the_counters(pool: DbPool) -> None:
    work = [fail(pool, ErrorKind.TIMEOUT) for _ in range(60)] + [
        health.record_success(pool, "c", now=NOW) for _ in range(40)
    ]
    await asyncio.gather(*work)

    h = await health_row(pool, "c")
    assert (h["failure_count"], h["success_count"]) == (60, 40)
    assert 0 <= h["consecutive_failures"] <= 60


async def test_error_text_is_redacted_and_bounded(pool: DbPool) -> None:
    from farm.secrets import register_secret

    register_secret("SENTINEL-health-secret-55")
    await health.record_failure(
        pool, "c", ErrorKind.SERVER, "bad key SENTINEL-health-secret-55 " + "x" * 2000, now=NOW
    )

    (row,) = await fetch(pool, "select last_error from connection_health")
    assert "SENTINEL-health-secret-55" not in row[0] and len(row[0]) <= health.MAX_ERROR_CHARS
