"""Q0-04, Q0-13, Q0-14, Q0-21, Q0-23: budget holds, late commit, the rolling window, reservation hygiene.

Real Postgres. Each test reproduces the failure the audit found (`docs/audits/Q0-audit.md`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import structlog

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.registry import Registry
from farm.resources import budget, ledger
from farm.resources.router import route
from farm.resources.trajectory import Trajectory
from tests.conftest import START, seed_connection
from tests.farm_helpers import ScriptedExecutor, fetch, only_reoon_01, quota, reservations

type Make = Callable[..., Awaitable[FarmContext]]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


async def paid_connection(pool: DbPool, conn_id: str = "c", provider: str = "p") -> None:
    await seed_connection(
        pool,
        provider,
        conn_id,
        {"credits": {"limit": None, "period": "day", "unit_cost_usd": "0.5", "estimate_per_call": 1}},
    )


async def set_budget(pool: DbPool, scope: str, ref: str | None, usd: float) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.budgets (scope, ref, monthly_usd, hard_stop) values (%s, %s, %s, true)",
            (scope, ref, usd),
        )


async def set_router_budgets(pool: DbPool, global_usd: float) -> None:
    """The synced registry already has a global budget and a 0 budget per provider: set them for a router test."""
    async with pool.connection() as conn:
        await conn.execute("update public.budgets set monthly_usd = %s where scope = 'global'", (global_usd,))
        await conn.execute("update public.budgets set monthly_usd = 100 where scope = 'provider'")


# --- Q0-04: the budget hard stop is reserved, not just checked ----------------------------------------------------


async def test_five_concurrent_half_dollar_calls_against_one_dollar_pass_two_and_block_three(pool: DbPool) -> None:
    await paid_connection(pool)
    await set_budget(pool, "global", None, 1.0)

    holds = await asyncio.gather(*[budget.reserve(pool, "c", Decimal("0.5"), now=NOW) for _ in range(5)])

    assert sum(1 for h in holds if h.decision.allowed) == 2
    blocked = [h for h in holds if not h.decision.allowed]
    assert len(blocked) == 3
    assert all(h.decision.error_kind == "budget_exhausted" and h.decision.scope == "global" for h in blocked)
    assert "in flight" in (blocked[0].decision.reason or "")


async def test_a_released_hold_frees_the_budget_for_the_next_call(pool: DbPool) -> None:
    await paid_connection(pool)
    await set_budget(pool, "global", None, 1.0)
    first = await budget.reserve(pool, "c", Decimal("0.5"), now=NOW)
    second = await budget.reserve(pool, "c", Decimal("0.5"), now=NOW)
    assert first.hold_id is not None and second.hold_id is not None
    assert not (await budget.reserve(pool, "c", Decimal("0.5"), now=NOW)).decision.allowed

    await budget.release(pool, first.hold_id)

    assert (await budget.reserve(pool, "c", Decimal("0.5"), now=NOW)).decision.allowed


async def test_settled_spend_and_open_holds_add_up(pool: DbPool) -> None:
    await paid_connection(pool)
    await set_budget(pool, "connection", "c", 1.0)
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.usage_events (connection_id, unit, amount, kind, cost_usd, at) "
            "values ('c', 'credits', 1, 'actual', 0.5, %s)",
            (NOW,),
        )
    assert (await budget.reserve(pool, "c", Decimal("0.5"), now=NOW)).decision.allowed  # 0.5 spent + 0.5 = 1.0
    assert not (await budget.reserve(pool, "c", Decimal("0.5"), now=NOW)).decision.allowed


async def test_provider_and_connection_budgets_are_held_too(pool: DbPool) -> None:
    await paid_connection(pool, "a")
    await paid_connection(pool, "b")  # same provider "p"
    await set_budget(pool, "provider", "p", 1.0)
    holds = await asyncio.gather(
        *[budget.reserve(pool, cid, Decimal("0.5"), now=NOW) for cid in ("a", "b", "a", "b")]
    )
    assert sum(1 for h in holds if h.decision.allowed) == 2
    assert {h.decision.scope for h in holds if not h.decision.allowed} == {"provider"}


async def test_a_hold_that_was_never_released_stops_counting_after_its_ttl(pool: DbPool) -> None:
    await paid_connection(pool)
    await set_budget(pool, "global", None, 0.5)
    assert (await budget.reserve(pool, "c", Decimal("0.5"), ttl_s=60, now=NOW)).decision.allowed
    later = NOW.replace(minute=2)
    assert (await budget.reserve(pool, "c", Decimal("0.5"), ttl_s=60, now=later)).decision.allowed


async def test_free_calls_are_never_held_or_blocked(pool: DbPool) -> None:
    await paid_connection(pool)
    await set_budget(pool, "global", None, 0)
    hold = await budget.reserve(pool, "c", 0, now=NOW)
    assert hold.decision.allowed and hold.hold_id is None


async def test_the_router_drops_its_budget_hold_after_every_outcome(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    from farm.executors.base import ErrorKind
    from tests.farm_helpers import failure, ok_result

    registry.providers["reoon"].connections[0].units["credits"].unit_cost_usd = Decimal("0.5")
    executor = ScriptedExecutor(ok_result(), failure(ErrorKind.BAD_REQUEST))
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    await set_router_budgets(pool, 10)

    for email in ("a@example.com", "b@example.com"):
        await route(ctx, "verify_email", {"email": email}, caller="test", pin="reoon-01")

    assert len(executor.calls) == 2
    assert await fetch(pool, "select count(*) from public.budget_reservations") == [(0,)]


async def test_the_router_blocks_a_paid_call_the_hard_stop_cannot_afford(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    from tests.farm_helpers import ok_result

    registry.providers["reoon"].connections[0].units["credits"].unit_cost_usd = Decimal("0.5")
    executor = ScriptedExecutor(ok_result())
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    await set_router_budgets(pool, 0.25)

    out = await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01")

    assert not out.ok and executor.calls == []
    assert await fetch(pool, "select count(*) from public.budget_reservations") == [(0,)]


# --- Q0-13: a commit after the reservation expired is recorded, with a warning ------------------------------------


async def test_commit_after_expiry_charges_the_usage_and_logs_a_warning(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "day"}})
    res = await ledger.reserve(pool, "c", "credits", 1, uuid4(), ttl_s=0)
    assert res is not None
    await asyncio.sleep(0.05)
    assert await ledger.expire(pool) == 1

    with structlog.testing.capture_logs() as logs:
        assert await ledger.commit(pool, res, 3) is True  # the call really ran and used 3

    assert await quota(pool, "c") == (3, 0)
    assert await reservations(pool, "c") == [("c", "credits", "committed", Decimal(3))]
    late = [e for e in logs if e["event"] == "ledger.late_commit"]
    assert len(late) == 1 and late[0]["log_level"] == "warning" and late[0]["actual"] == "3"


async def test_a_normal_commit_is_not_late_and_a_second_commit_changes_nothing(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "day"}})
    res = await ledger.reserve(pool, "c", "credits", 1, uuid4())
    assert res is not None
    with structlog.testing.capture_logs() as logs:
        assert await ledger.commit(pool, res, 1) is False
        assert await ledger.commit(pool, res, 1) is False
    assert await quota(pool, "c") == (1, 0)
    assert not [e for e in logs if e["event"] == "ledger.late_commit"]


async def test_commit_after_release_is_still_a_no_op(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "day"}})
    res = await ledger.reserve(pool, "c", "credits", 1, uuid4())
    assert res is not None
    await ledger.release(pool, res)
    assert await ledger.commit(pool, res, 1) is False
    assert await quota(pool, "c") == (0, 0)


# --- Q0-14: rolling_5h is a sliding window ------------------------------------------------------------------------


async def age_reservations(pool: DbPool, hours: float) -> None:
    """Make every committed reservation ``hours`` old (the updated_at trigger would stamp now())."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("alter table public.quota_reservations disable trigger trg_quota_reservations_updated_at")
        await conn.execute(
            "update public.quota_reservations set updated_at = now() - make_interval(secs => %s) "
            "where status = 'committed'",
            (hours * 3600,),
        )
        await conn.execute("alter table public.quota_reservations enable trigger trg_quota_reservations_updated_at")


async def test_rolling_5h_frees_quota_when_usage_leaves_the_window(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"messages": {"limit": 10, "period": "rolling_5h"}})
    first = await ledger.reserve(pool, "c", "messages", 6, uuid4())
    assert first is not None
    await ledger.commit(pool, first, 6)
    assert await ledger.reserve(pool, "c", "messages", 5, uuid4()) is None  # 6 + 5 > 10 inside the window

    await age_reservations(pool, 4)  # still inside 5 hours
    assert await ledger.reserve(pool, "c", "messages", 5, uuid4()) is None

    await age_reservations(pool, 6)  # left the window
    assert await ledger.reserve(pool, "c", "messages", 5, uuid4()) is not None


async def test_rolling_5h_counts_open_holds_and_stays_race_free(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"messages": {"limit": 10, "period": "rolling_5h"}})
    results = await asyncio.gather(*[ledger.reserve(pool, "c", "messages", 1, uuid4()) for _ in range(25)])
    assert sum(r is not None for r in results) == 10


async def test_rolling_5h_late_commit_counts_in_the_window(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"messages": {"limit": 10, "period": "rolling_5h"}})
    res = await ledger.reserve(pool, "c", "messages", 1, uuid4(), ttl_s=0)
    assert res is not None
    await asyncio.sleep(0.05)
    assert await ledger.expire(pool) == 1
    await ledger.commit(pool, res, 9)
    assert await ledger.reserve(pool, "c", "messages", 2, uuid4()) is None
    assert await ledger.reserve(pool, "c", "messages", 1, uuid4()) is not None


async def test_other_periods_still_use_their_period_row(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 2, "period": "day"}})
    for _ in range(2):
        res = await ledger.reserve(pool, "c", "credits", 1, uuid4())
        assert res is not None
        await ledger.commit(pool, res, 1)
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None


# --- Q0-21: no reservation leaks between reserve and the settling try ---------------------------------------------


@pytest.mark.parametrize("pin", [None, "reoon-01"])
async def test_a_failure_right_after_the_reservation_releases_it(
    pool: DbPool, farm_factory: Make, registry: Registry, monkeypatch: pytest.MonkeyPatch, pin: str | None
) -> None:
    executor = ScriptedExecutor()
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    real_event = Trajectory.event

    async def failing_event(self: Trajectory, kind: str, *args: Any, **data: Any) -> Any:
        if kind == "execute":
            raise RuntimeError("database error while writing the execute event")
        return await real_event(self, kind, *args, **data)

    monkeypatch.setattr(Trajectory, "event", failing_event)

    await route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin=pin)

    assert executor.calls == []  # the provider was never called ...
    rows = await reservations(pool, "reoon-01")
    assert rows and {r[2] for r in rows} == {"released"}  # ... so nothing is held and nothing is charged
    assert await quota(pool, "reoon-01") == (0, 0)


async def test_a_cancellation_right_after_the_reservation_releases_it(
    pool: DbPool, farm_factory: Make, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = ScriptedExecutor()
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    real_event = Trajectory.event
    at_execute = asyncio.Event()

    async def stalling_event(self: Trajectory, kind: str, *args: Any, **data: Any) -> Any:
        if kind == "execute":
            at_execute.set()
            await asyncio.sleep(3600)
        return await real_event(self, kind, *args, **data)

    monkeypatch.setattr(Trajectory, "event", stalling_event)
    call = asyncio.create_task(route(ctx, "verify_email", {"email": "a@example.com"}, caller="test", pin="reoon-01"))
    await asyncio.wait_for(at_execute.wait(), 10)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    assert executor.calls == []
    assert {r[2] for r in await reservations(pool, "reoon-01")} == {"released"}
    assert await quota(pool, "reoon-01") == (0, 0)


# --- Q0-23: no debug code in farm_reactivate_due ------------------------------------------------------------------


async def test_farm_reactivate_due_has_no_debug_code_and_raises_no_notice(pool: DbPool) -> None:
    rows = await fetch(pool, "select prosrc from pg_proc where proname = 'farm_reactivate_due'")
    source = rows[0][0].lower()
    assert "raise notice" not in source and "string_agg" not in source

    await seed_connection(pool, "p", "c", {"credits": 10}, status="exhausted")
    notices: list[str] = []
    async with pool.connection() as conn:
        conn.add_notice_handler(lambda diag: notices.append(str(diag.message_primary)))
        cur = await conn.execute("select public.farm_reactivate_due(%s)", (START,))
        row = await cur.fetchone()
    assert row == (1,) and notices == []
    assert (await fetch(pool, "select status from connections where id = 'c'"))[0][0] == "active"


async def test_farm_reactivate_due_clears_a_stale_next_reset_at(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10}, status="exhausted")
    async with pool.connection() as conn:
        await conn.execute(
            "update public.consumption_units set next_reset_at = %s where connection_id = 'c'",
            (START.replace(year=2026, month=9),),
        )
        await conn.execute("select public.farm_reactivate_due(%s)", (START,))
    assert await fetch(pool, "select next_reset_at from public.consumption_units where connection_id = 'c'") == [
        (None,)
    ]
