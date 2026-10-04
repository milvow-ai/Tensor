"""Tests for farm/manager/scheduler.py: periodic tasks and auto-resume of exhausted connections."""

from datetime import UTC, datetime
from uuid import uuid4

from farm.db.pool import DbPool
from farm.manager.scheduler import reactivate_due_connections, run_maintenance_tick
from farm.resources import ledger
from tests.conftest import seed_connection


async def test_exhausted_connection_auto_resumes_at_reset_time(pool: DbPool) -> None:
    """Acceptance test: exhausted connection auto-resumes at its reset time (injected clock)."""
    # Create connection with status='exhausted' and next_reset_at = Oct 15, 2026 00:00 UTC
    reset_time = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    await seed_connection(
        pool,
        "p_sched",
        "c_exhausted_1",
        units={"credits": {"limit": 100, "period": "day"}},
        status="exhausted",
    )

    # Set next_reset_at in consumption_units
    async with pool.connection() as conn:
        await conn.execute(
            "update public.consumption_units set next_reset_at = %s where connection_id = 'c_exhausted_1'",
            (reset_time,),
        )

    # Before reset time (Oct 14, 23:59 UTC): should NOT resume
    before_clock = datetime(2026, 10, 14, 23, 59, tzinfo=UTC)
    reactivated = await reactivate_due_connections(pool, now=before_clock)
    assert "c_exhausted_1" not in reactivated

    async with pool.connection() as conn:
        cur = await conn.execute("select status from public.connections where id = 'c_exhausted_1'")
        assert (await cur.fetchone())[0] == "exhausted"

    # At or after reset time (Oct 15, 00:01 UTC): should auto-resume!
    after_clock = datetime(2026, 10, 15, 0, 1, tzinfo=UTC)
    reactivated2 = await reactivate_due_connections(pool, now=after_clock)
    assert "c_exhausted_1" in reactivated2

    async with pool.connection() as conn:
        cur = await conn.execute("select status from public.connections where id = 'c_exhausted_1'")
        assert (await cur.fetchone())[0] == "active"

        # Check audit event was written
        cur_audit = await conn.execute(
            "select action, target, before, after from public.audit_events where target = 'c_exhausted_1'"
        )
        audit_row = await cur_audit.fetchone()
        assert audit_row is not None
        assert audit_row[0] == "auto_resume"
        assert audit_row[1] == "c_exhausted_1"
        assert audit_row[2] == {"status": "exhausted"}
        assert audit_row[3] == {"status": "active"}


async def test_run_maintenance_tick_expires_reservations(pool: DbPool) -> None:
    await seed_connection(pool, "p_sched", "c_res_exp", {"credits": 50})

    # Make reservation with 1s TTL and age it
    res = await ledger.reserve(pool, "c_res_exp", "credits", 10, uuid4(), ttl_s=1)
    assert res is not None

    async with pool.connection() as conn:
        await conn.execute(
            "update public.quota_reservations set expires_at = now() - interval '5 seconds' where id = %s",
            (res,),
        )

    # Run maintenance tick
    tick_result = await run_maintenance_tick(pool)
    assert tick_result["expired_reservations"] >= 1

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select status from public.quota_reservations where id = %s",
            (res,),
        )
        assert (await cur.fetchone())[0] == "expired"
