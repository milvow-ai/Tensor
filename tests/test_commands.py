"""Tests for farm/control/commands.py: queue consumer, payload validation, and command execution."""

import os
from uuid import uuid4

from farm.control.commands import execute_command, poll_and_execute_queued, process_command
from farm.db.pool import DbPool
from tests.conftest import seed_connection


async def test_all_eleven_command_kinds_execute_and_audit(pool: DbPool) -> None:
    """Acceptance test: one executed + acknowledged row per command kind; audit row per change."""
    # 0. Setup initial provider, connection, capability, and alert
    await seed_connection(
        pool,
        "p_cmd",
        "c_cmd_1",
        units={"credits": 500},
        priority=100,
        status="active",
        auth_ref="env:TEST_KEY",
    )

    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.capabilities (name, kind, description) values ('cap_1', 'tool', 'test cap')"
        )
        alert_id = uuid4()
        await conn.execute(
            "insert into public.alerts (id, kind, severity, message) values (%s, 'test_alert', 'warn', 'msg')",
            (alert_id,),
        )

    # 1. pause
    p_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'pause', '{\"connection_id\": \"c_cmd_1\"}'::jsonb, 'queued', 'admin_user')",
            (p_id,),
        )
    status, res = await process_command(pool, p_id)
    assert status == "done"
    assert res["status"] == "paused"

    # 2. resume
    r_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'resume', '{\"connection_id\": \"c_cmd_1\"}'::jsonb, 'queued', 'admin_user')",
            (r_id,),
        )
    status, res = await process_command(pool, r_id)
    assert status == "done"
    assert res["status"] == "active"

    # 3. set_priority
    sp_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'set_priority', '{\"connection_id\": \"c_cmd_1\", \"priority\": 25}'::jsonb, 'queued', 'admin_user')",
            (sp_id,),
        )
    status, res = await process_command(pool, sp_id)
    assert status == "done"
    assert res["priority"] == 25

    # 4. set_strategy
    ss_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'set_strategy', '{\"connection_id\": \"c_cmd_1\", \"strategy\": \"most_remaining\"}'::jsonb, 'queued', 'admin_user')",
            (ss_id,),
        )
    status, res = await process_command(pool, ss_id)
    assert status == "done"
    assert res["strategy"] == "most_remaining"

    # 5. set_budget
    sb_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'set_budget', '{\"scope\": \"connection\", \"ref\": \"c_cmd_1\", \"monthly_usd\": 75.0, \"hard_stop\": true}'::jsonb, 'queued', 'admin_user')",
            (sb_id,),
        )
    status, res = await process_command(pool, sb_id)
    assert status == "done"
    assert res["monthly_usd"] == 75.0

    # 6. add_connection
    ac_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'add_connection', '{\"provider_id\": \"p_cmd\", \"id\": \"c_cmd_2\", \"auth_ref\": \"env:TEST_KEY_2\", \"priority\": 50, \"units\": {\"credits\": 1000}}'::jsonb, 'queued', 'admin_user')",
            (ac_id,),
        )
    status, res = await process_command(pool, ac_id)
    assert status == "done"
    assert res["connection_id"] == "c_cmd_2"

    # 7. update_connection
    uc_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'update_connection', '{\"connection_id\": \"c_cmd_2\", \"label\": \"Updated Label\", \"priority\": 30}'::jsonb, 'queued', 'admin_user')",
            (uc_id,),
        )
    status, res = await process_command(pool, uc_id)
    assert status == "done"
    assert "label" in res["updated"]

    # 8. set_route
    sr_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'set_route', '{\"capability\": \"cap_1\", \"provider_id\": \"p_cmd\", \"position\": 1, \"enabled\": true}'::jsonb, 'queued', 'admin_user')",
            (sr_id,),
        )
    status, res = await process_command(pool, sr_id)
    assert status == "done"

    # 9. test_connection
    os.environ["TEST_KEY"] = "secret-val"
    tc_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'test_connection', '{\"connection_id\": \"c_cmd_1\"}'::jsonb, 'queued', 'admin_user')",
            (tc_id,),
        )
    status, res = await process_command(pool, tc_id)
    assert status == "done"
    assert res["auth_ok"] is True

    # 10. ack_alert
    aa_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'ack_alert', jsonb_build_object('alert_id', %s::text), 'queued', 'admin_user')",
            (aa_id, alert_id),
        )
    status, res = await process_command(pool, aa_id)
    assert status == "done"
    assert res["status"] == "acknowledged"

    # 11. remove_connection
    rc_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status, created_by) "
            "values (%s, 'remove_connection', '{\"connection_id\": \"c_cmd_2\"}'::jsonb, 'queued', 'admin_user')",
            (rc_id,),
        )
    status, res = await process_command(pool, rc_id)
    assert status == "done"
    assert res["status"] == "deleted"

    # Check audit events: one row per change
    async with pool.connection() as conn:
        cur = await conn.execute("select count(*) from public.audit_events where actor = 'admin_user'")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 11


async def test_invalid_command_rejected_with_reason(pool: DbPool) -> None:
    """Acceptance test: one invalid row rejected with reason."""
    # 1. Invalid payload queued in farm_commands
    cmd_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) "
            "values (%s, 'pause', '{\"invalid_key\": 123}'::jsonb, 'queued')",
            (cmd_id,),
        )

    status, res = await process_command(pool, cmd_id)
    assert status == "rejected"
    assert "Invalid payload" in res["error"]

    # 2. Unknown command kind tested via execute_command
    status_unk, res_unk = await execute_command(pool, uuid4(), "nonexistent_kind", {})
    assert status_unk == "rejected"
    assert "Unknown command kind" in res_unk["error"]

    # Command on nonexistent connection
    cmd2_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) "
            "values (%s, 'pause', '{\"connection_id\": \"does_not_exist\"}'::jsonb, 'queued')",
            (cmd2_id,),
        )

    status2, res2 = await process_command(pool, cmd2_id)
    assert status2 == "rejected"
    assert "not found" in res2["error"].lower()


async def test_add_connection_refuses_raw_secret_or_bad_auth_ref(pool: DbPool) -> None:
    """Rule: add_connection refuses an auth_ref that is not env:/token-store:/cli: form or looks like a raw secret."""
    await seed_connection(pool, "p_sec", "c_sec_base")

    # 1. Plain string not in env:/token-store:/cli: form
    cmd1_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) "
            "values (%s, 'add_connection', '{\"provider_id\": \"p_sec\", \"id\": \"c_bad_1\", \"auth_ref\": \"my_plain_secret_key\"}'::jsonb, 'queued')",
            (cmd1_id,),
        )
    status1, res1 = await process_command(pool, cmd1_id)
    assert status1 == "rejected"
    assert "auth_ref must start with" in res1["error"]

    # 2. Raw secret disguised in auth_ref
    cmd2_id = uuid4()
    bad_ref = "cli:" + "Bearer " + "my-raw-secret-token"
    from psycopg.types.json import Jsonb

    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) "
            "values (%s, 'add_connection', %s, 'queued')",
            (
                cmd2_id,
                Jsonb({"provider_id": "p_sec", "id": "c_bad_2", "auth_ref": bad_ref}),
            ),
        )
    status2, res2 = await process_command(pool, cmd2_id)
    assert status2 == "rejected"
    assert "raw secret" in res2["error"].lower()


async def test_poll_and_execute_queued_batch(pool: DbPool) -> None:
    await seed_connection(pool, "p_batch", "c_batch_1")

    # Insert 2 queued commands
    c1, c2 = uuid4(), uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "insert into public.farm_commands (id, kind, payload, status) values "
            "(%s, 'pause', '{\"connection_id\": \"c_batch_1\"}'::jsonb, 'queued'), "
            "(%s, 'resume', '{\"connection_id\": \"c_batch_1\"}'::jsonb, 'queued')",
            (c1, c2),
        )

    processed = await poll_and_execute_queued(pool)
    assert processed == 2

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select status from public.farm_commands where id in (%s, %s)",
            (c1, c2),
        )
        statuses = [r[0] for r in await cur.fetchall()]
        assert statuses == ["done", "done"]
