"""Q0E (Q0-20): a run whose process died does not stay 'running' for ever."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from farm.db.pool import DbPool
from farm.resources.trajectory import RUN_LEASE_S, Trajectory, fail_orphaned_runs, fetch_run
from tests.farm_helpers import fetch


async def _start_run(pool: DbPool, started_at: datetime) -> Trajectory:
    return await Trajectory.start(
        pool, lambda: started_at, capability="verify_email", caller="test", strategy="priority"
    )


async def test_a_run_left_running_by_a_dead_process_is_failed_as_interrupted_after_the_lease(pool: DbPool) -> None:
    now = datetime.now(UTC)
    orphan = await _start_run(pool, now - timedelta(seconds=RUN_LEASE_S + 60))
    await orphan.event("plan", None, note="about to call the provider")  # the process dies after this

    assert await fail_orphaned_runs(pool, now=now) == 1

    run = await fetch_run(pool, orphan.run_id)
    assert run is not None
    assert (run.status, run.error_kind) == ("failed", "interrupted")
    assert run.finished_at is not None
    assert run.error is not None and "process" in run.error and "ledger" in run.error
    last = run.events[-1]
    assert (last.kind, last.seq, last.data["error_kind"]) == ("failure", 2, "interrupted")


async def test_a_run_inside_its_lease_and_finished_runs_are_left_alone(pool: DbPool) -> None:
    now = datetime.now(UTC)
    fresh = await _start_run(pool, now - timedelta(seconds=RUN_LEASE_S - 60))
    finished = await _start_run(pool, now - timedelta(days=2))
    await finished.finish(status="succeeded", cost_usd=Decimal("0.5"), cached=False, connection_id=None)

    assert await fail_orphaned_runs(pool, now=now) == 0

    assert (await fetch(pool, "select status from runs where id = %s", fresh.run_id))[0][0] == "running"
    assert (await fetch(pool, "select status, error_kind from runs where id = %s", finished.run_id))[0] == (
        "succeeded",
        None,
    )


async def test_the_sweep_is_idempotent(pool: DbPool) -> None:
    now = datetime.now(UTC)
    await _start_run(pool, now - timedelta(days=1))
    assert await fail_orphaned_runs(pool, now=now) == 1
    assert await fail_orphaned_runs(pool, now=now) == 0
    assert (await fetch(pool, "select count(*) from run_events where kind = 'failure'"))[0][0] == 1
