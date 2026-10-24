"""Q0G follow-ups (D39-D42): probing accounts are not 'active', one budget rule, an overrun blocks the account,
and rolling-window meters show the sliding sum."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from farm.db.pool import DbPool
from farm.manager.budgets import check_paid_call
from farm.resources import budget, ledger
from tests.conftest import seed_connection
from tests.farm_helpers import fetch

# --- D39: v_connection_status ----------------------------------------------------------------------------


async def test_half_open_and_open_circuits_are_not_active(pool: DbPool) -> None:
    for cid in ("c_closed", "c_open", "c_probe"):
        await seed_connection(pool, "p", cid, {"credits": 10})
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.connection_health (connection_id, circuit, cooldown_until) values "
            "('c_open', 'open', now() + interval '5 minutes'), "
            "('c_probe', 'half_open', now() + interval '1 minute')"
        )
    rows = await fetch(
        pool, "select connection_id, effective_state from public.v_connection_status order by connection_id"
    )
    assert dict(rows) == {"c_closed": "active", "c_open": "circuit_open", "c_probe": "probing"}


async def test_probing_account_is_not_counted_usable(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c_probe", {"credits": 10})
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.connection_health (connection_id, circuit, cooldown_until) "
            "values ('c_probe', 'half_open', now() - interval '1 minute')"
        )
    rows = await fetch(
        pool, "select count(*) from public.v_connection_status where effective_state = 'active'"
    )
    assert rows == [(0,)]


# --- D40: one budget rule --------------------------------------------------------------------------------


async def _hard_stop(pool: DbPool, connection_id: str, monthly: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.budgets (scope, ref, monthly_usd, hard_stop) values ('connection', %s, %s, true)",
            (connection_id, Decimal(monthly)),
        )


async def test_check_paid_call_counts_spend_held_by_in_flight_calls(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 100, "unit_cost_usd": 0.3}})
    await _hard_stop(pool, "c", "1.00")
    hold = await budget.reserve(pool, "c", Decimal("0.70"))
    assert hold.decision.allowed
    # nothing is settled yet (settled spend is 0): only the hold makes 0.70 + 0.40 > 1.00 visible
    blocked = await check_paid_call(pool, "c", Decimal("0.40"))
    assert not blocked.allowed and blocked.error_kind == "budget_exhausted" and blocked.scope == "connection"
    assert (await check_paid_call(pool, "c", Decimal("0.30"))).allowed
    # the check itself holds nothing: the same answer twice, and the budget is free again after releasing
    assert (await check_paid_call(pool, "c", Decimal("0.30"))).allowed
    assert hold.hold_id is not None
    await budget.release(pool, hold.hold_id)
    assert (await check_paid_call(pool, "c", Decimal("0.40"))).allowed
    assert await fetch(pool, "select count(*) from public.budget_reservations") == [(0,)]


async def test_concurrent_calls_through_both_paths_cannot_overshoot_a_hard_stop(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 100, "unit_cost_usd": 0.3}})
    await _hard_stop(pool, "c", "1.00")
    cost = Decimal("0.30")
    holds, _checks = await asyncio.gather(
        asyncio.gather(*(budget.reserve(pool, "c", cost, request_id=uuid4()) for _ in range(8))),
        asyncio.gather(*(check_paid_call(pool, "c", cost) for _ in range(8))),
    )
    granted = [h for h in holds if h.decision.allowed]
    assert len(granted) == 3, "exactly three $0.30 holds fit a $1.00 hard stop"
    held = await fetch(pool, "select coalesce(sum(amount_usd), 0) from public.budget_reservations")
    assert held[0][0] == Decimal("0.90")
    # with the budget full of holds, a check agrees with the router instead of reading settled spend only
    assert not (await check_paid_call(pool, "c", cost)).allowed


# --- D41: an executor reports more than its estimate -----------------------------------------------------


async def test_overrun_is_recorded_and_blocks_the_account_until_the_window_frees(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "day"}})
    res = await ledger.reserve(pool, "c", "credits", 5, uuid4())
    assert res is not None
    await ledger.commit(pool, res, 14)  # the estimate was 5, the call really cost 14
    used = await fetch(pool, "select used, reserved from public.quota_usage where connection_id = 'c'")
    assert used == [(Decimal(14), Decimal(0))], "the real usage must be recorded, never dropped"
    state = await fetch(
        pool,
        "select c.status, h.cooldown_until, u.next_reset_at from public.connections c "
        "join public.connection_health h on h.connection_id = c.id "
        "join public.consumption_units u on u.connection_id = c.id where c.id = 'c'",
    )
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    assert state == [("exhausted", tomorrow, tomorrow)]
    assert await ledger.reserve(pool, "c", "credits", 1, uuid4()) is None


async def test_usage_within_the_limit_does_not_block(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "day"}})
    res = await ledger.reserve(pool, "c", "credits", 5, uuid4())
    assert res is not None
    await ledger.commit(pool, res, 10)  # exactly the limit is not an overrun
    assert await fetch(pool, "select status from public.connections where id = 'c'") == [("active",)]
    assert await fetch(pool, "select count(*) from public.connection_health") == [(0,)]


async def test_rolling_overrun_blocks_until_enough_old_usage_leaves_the_window(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"msgs": {"limit": 10, "period": "rolling_5h"}})
    first = await ledger.reserve(pool, "c", "msgs", 4, uuid4())
    assert first is not None
    await ledger.commit(pool, first, 4)
    second = await ledger.reserve(pool, "c", "msgs", 4, uuid4())
    assert second is not None
    await ledger.commit(pool, second, 9)  # window now 13 of 10: 3 over
    rows = await fetch(
        pool,
        "select c.status, h.cooldown_until, now() from public.connections c "
        "join public.connection_health h on h.connection_id = c.id where c.id = 'c'",
    )
    status, until, now = rows[0]
    assert status == "exhausted"
    # the first 4 (>= the 3 excess) leave the window 5 hours after they were committed
    assert timedelta(hours=4, minutes=58) < until - now < timedelta(hours=5, minutes=1)
    assert await ledger.reserve(pool, "c", "msgs", 1, uuid4()) is None


# --- D42: rolling_5h meters ------------------------------------------------------------------------------


async def test_rolling_meter_shows_the_sliding_sum_not_the_lifetime_counter(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"msgs": {"limit": 20, "period": "rolling_5h"}})
    old = await ledger.reserve(pool, "c", "msgs", 5, uuid4())
    recent = await ledger.reserve(pool, "c", "msgs", 3, uuid4())
    held = await ledger.reserve(pool, "c", "msgs", 2, uuid4())
    assert old and recent and held
    await ledger.commit(pool, old, 5)
    await ledger.commit(pool, recent, 3)
    trigger = "trg_quota_reservations_updated_at"
    async with pool.connection() as conn:  # the first 5 were used 6 hours ago
        await conn.execute(f"alter table public.quota_reservations disable trigger {trigger}")
        await conn.execute(
            "update public.quota_reservations set updated_at = now() - interval '6 hours' where id = %s",
            (old,),
        )
        await conn.execute(f"alter table public.quota_reservations enable trigger {trigger}")
    lifetime = await fetch(pool, "select used from public.quota_usage where connection_id = 'c'")
    assert lifetime == [(Decimal(8),)]
    meter = await fetch(
        pool,
        "select used, reserved, remaining, calls_remaining from public.v_connection_status where unit = 'msgs'",
    )
    assert meter == [(Decimal(3), Decimal(2), Decimal(15), Decimal(15))]


async def test_past_next_reset_at_is_hidden_and_cleared_when_the_window_resets(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": {"limit": 10, "period": "day"}})
    async with pool.connection() as conn:
        await conn.execute(
            "update public.consumption_units set next_reset_at = now() - interval '1 hour' where connection_id = 'c'"
        )
    shown = await fetch(pool, "select next_reset_at from public.v_connection_status where unit = 'credits'")
    assert shown == [(None,)]
    async with pool.connection() as conn:
        await conn.execute("select public.farm_reactivate_due(now())")
    stored = await fetch(pool, "select next_reset_at from public.consumption_units where connection_id = 'c'")
    assert stored == [(None,)]
    async with pool.connection() as conn:  # a reset still ahead stays visible
        await conn.execute(
            "update public.consumption_units set next_reset_at = now() + interval '1 hour' where connection_id = 'c'"
        )
    ahead = await fetch(
        pool, "select next_reset_at is not null from public.v_connection_status where unit = 'credits'"
    )
    assert ahead == [(True,)]
