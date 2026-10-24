"""Q0E: the command consumer claims atomically, rejects without side effects, writes atomically, and expires leases.

Each test reproduces a finding of the Q0 audit (Q0-02, Q0-10, Q0-17) and fails on the code before this round.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from farm.control.commands import (
    COMMAND_LEASE_S,
    CommandConsumer,
    _run_claimed,
    claim_command,
    execute_command,
    fail_expired_command_claims,
    poll_and_execute_queued,
    process_command,
)
from farm.db.pool import DbPool
from tests.conftest import seed_connection
from tests.farm_helpers import fetch


async def _queue(pool: DbPool, kind: str, payload: dict[str, Any], *, status: str = "queued") -> UUID:
    cid = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) values (%s, %s, %s, %s, 'tester')",
            (cid, kind, Jsonb(payload), status),
        )
    return cid


async def _status(pool: DbPool, cid: UUID) -> tuple[str, dict[str, Any] | None]:
    rows = await fetch(pool, "select status, result from public.farm_commands where id = %s", cid)
    return rows[0][0], rows[0][1]


async def _drain(pool: DbPool) -> int:
    done = 0
    while (n := await poll_and_execute_queued(pool, limit=5)) > 0:
        done += n
    return done


# --- Q0-02: a command runs exactly once ---------------------------------------------------------------------------


async def test_two_consumers_racing_over_200_commands_run_each_command_exactly_once(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    ids = [await _queue(pool, "set_priority", {"connection_id": "c", "priority": n}) for n in range(200)]

    first, second = await asyncio.gather(_drain(pool), _drain(pool))

    assert first + second == 200, f"the consumers ran {first} + {second} commands for 200 queued"
    audits = await fetch(pool, "select count(*) from audit_events where action = 'set_priority'")
    assert audits[0][0] == 200, f"{audits[0][0]} audit rows for 200 commands: some command ran twice"
    states = await fetch(pool, "select status, count(*) from farm_commands group by status")
    assert states == [("done", 200)]
    assert len(set(ids)) == 200


async def test_a_command_that_is_not_queued_is_never_executed_again(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    cid = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 5})
    assert (await process_command(pool, cid))[0] == "done"

    again_status, again_result = await process_command(pool, cid)

    audits = await fetch(pool, "select count(*) from audit_events where action = 'set_priority'")
    assert audits[0][0] == 1
    assert again_status == "done" and "not queued" in again_result["error"]
    assert (await _status(pool, cid))[0] == "done"


async def test_a_command_held_by_another_consumer_is_not_executed_by_a_second_claim(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    cid = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 5})
    held = await claim_command(pool, cid)
    assert held is not None

    status, result = await process_command(pool, cid)

    assert status == "running" and "not queued" in result["error"]
    audits = await fetch(pool, "select count(*) from audit_events where action = 'set_priority'")
    assert audits[0][0] == 0


async def test_an_unknown_command_id_is_rejected(pool: DbPool) -> None:
    assert await process_command(pool, uuid4()) == ("rejected", {"error": "Command not found"})


# --- Q0-10: a rejected command changes nothing; a failed one leaves nothing half done ------------------------------


async def test_set_strategy_with_an_unknown_provider_changes_nothing(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    status, _ = await execute_command(
        pool, uuid4(), "set_strategy", {"strategy": "round_robin", "connection_id": "c", "provider_id": "nope"}
    )
    assert status == "rejected"
    assert (await fetch(pool, "select strategy from connections where id = 'c'"))[0][0] is None
    assert (await fetch(pool, "select count(*) from audit_events"))[0][0] == 0


async def test_set_max_parallel_with_an_unknown_provider_changes_nothing(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10}, concurrency=1)
    status, _ = await execute_command(
        pool, uuid4(), "set_max_parallel", {"max_parallel": 4, "connection_id": "c", "provider_id": "nope"}
    )
    assert status == "rejected"
    assert (await fetch(pool, "select concurrency, meta from connections where id = 'c'"))[0] == (1, {})
    assert (await fetch(pool, "select count(*) from audit_events"))[0][0] == 0


async def test_a_command_that_fails_midway_writes_nothing_not_even_its_connection_row(pool: DbPool) -> None:
    await seed_connection(pool, "p", "existing", {"credits": 10})
    async with pool.connection() as conn:  # a unit insert that fails after the connection row was written
        await conn.execute(
            "create function public.q0e_no_units() returns trigger language plpgsql as $$ "
            "begin raise exception 'unit rejected'; end $$"
        )
        await conn.execute(
            "create trigger q0e_no_units before insert on public.consumption_units "
            "for each row execute function public.q0e_no_units()"
        )
    try:
        status, result = await execute_command(
            pool,
            uuid4(),
            "add_connection",
            {"provider_id": "p", "id": "fresh", "auth_ref": "env:FRESH_KEY", "units": {"credits": 5}},
        )
    finally:
        async with pool.connection() as conn:
            await conn.execute("drop trigger q0e_no_units on public.consumption_units")
            await conn.execute("drop function public.q0e_no_units()")

    assert status == "failed" and "unit rejected" in result["error"]
    assert await fetch(pool, "select id from connections where id = 'fresh'") == []
    assert (await fetch(pool, "select count(*) from audit_events where action = 'add_connection'"))[0][0] == 0


async def test_a_successful_command_writes_its_change_and_its_audit_row_together(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    status, _ = await execute_command(
        pool, uuid4(), "add_connection", {"provider_id": "p", "id": "c2", "auth_ref": "env:C2_KEY", "units": {"credits": 5}}
    )
    assert status == "done"
    assert (await fetch(pool, "select count(*) from consumption_units where connection_id = 'c2'"))[0][0] == 1
    assert (await fetch(pool, "select count(*) from audit_events where action = 'add_connection'"))[0][0] == 1


# --- Q0-17: a command whose process died does not stay running for ever -------------------------------------------


async def test_a_command_claimed_by_a_dead_process_is_failed_with_a_reason_after_the_lease(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    cid = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 5})
    claimed = await claim_command(pool, cid)  # ... and the process that claimed it dies here
    assert claimed is not None
    now = datetime.now(UTC)

    assert await fail_expired_command_claims(pool, now=now + timedelta(seconds=COMMAND_LEASE_S - 60)) == 0
    assert (await _status(pool, cid))[0] == "running"  # still inside the lease: its owner may be working

    assert await fail_expired_command_claims(pool, now=now + timedelta(seconds=COMMAND_LEASE_S + 60)) == 1
    status, result = await _status(pool, cid)
    assert status == "failed" and result is not None
    assert "lease expired" in result["error"] and "queue it again" in result["error"]
    assert (await fetch(pool, "select done_at is not null from farm_commands where id = %s", cid))[0][0]


async def test_the_lease_sweep_leaves_queued_and_finished_commands_alone(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    queued = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 1})
    done = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 2})
    await process_command(pool, done)

    assert await fail_expired_command_claims(pool, now=datetime.now(UTC) + timedelta(days=1)) == 0
    assert (await _status(pool, queued))[0] == "queued"
    assert (await _status(pool, done))[0] == "done"


async def test_a_command_running_from_before_the_lease_existed_is_failed_by_its_creation_time(pool: DbPool) -> None:
    cid = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 1}, status="running")
    assert await fail_expired_command_claims(pool, now=datetime.now(UTC) + timedelta(hours=1)) == 1
    assert (await _status(pool, cid))[0] == "failed"


async def test_a_result_that_arrives_after_the_lease_sweep_does_not_overwrite_the_verdict(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    cid = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 5})
    claimed = await claim_command(pool, cid)
    assert claimed is not None
    await fail_expired_command_claims(pool, now=datetime.now(UTC) + timedelta(seconds=COMMAND_LEASE_S + 60))

    status, result = await _run_claimed(pool, claimed)  # the slow process finally finishes

    assert status == "failed" and "lease expired" in result["error"]
    stored, stored_result = await _status(pool, cid)
    assert stored == "failed" and stored_result is not None and "lease expired" in stored_result["error"]


async def test_the_consumer_loop_runs_the_lease_sweep(pool: DbPool) -> None:
    cid = await _queue(pool, "set_priority", {"connection_id": "c", "priority": 1}, status="running")
    async with pool.connection() as conn:
        await conn.execute(
            "update public.farm_commands set claimed_at = now() - interval '2 hours', claimed_by = 'dead' where id = %s",
            (cid,),
        )
    consumer = CommandConsumer(pool, poll_interval_s=0.05, sweep_interval_s=0.05)
    await consumer.start()
    try:
        for _ in range(100):
            if (await _status(pool, cid))[0] == "failed":
                break
            await asyncio.sleep(0.05)
    finally:
        await consumer.stop()
    assert (await _status(pool, cid))[0] == "failed"
