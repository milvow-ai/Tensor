"""Round-trip command contract tests between Console and Farm.

Verifies that every Console fixture in `console/src/lib/farm/__tests__/fixtures/*.json`:
  1. Validates against the Farm's Pydantic payload models (PAYLOAD_VALIDATORS).
  2. Is accepted and executed by `process_command` against the test database,
     transitioning to 'done' and producing the expected DB changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from farm.control.commands import PAYLOAD_VALIDATORS, process_command
from farm.db.pool import DbPool

FIXTURES_DIR = (
    Path(__file__).resolve().parents[1] / "console" / "src" / "lib" / "farm" / "__tests__" / "fixtures"
)

EXPECTED_KINDS = {
    "pause",
    "resume",
    "set_priority",
    "set_strategy",
    "set_budget",
    "add_connection",
    "update_connection",
    "remove_connection",
    "set_route",
    "test_connection",
    "ack_alert",
}


def test_fixture_files_exist_and_validate_schemas() -> None:
    """Every command kind has a corresponding fixture file that passes Pydantic validation."""
    fixture_files = list(FIXTURES_DIR.glob("*.json"))
    found_kinds = {f.stem for f in fixture_files}

    assert found_kinds == EXPECTED_KINDS, f"Missing fixtures: {EXPECTED_KINDS - found_kinds}"

    for f in fixture_files:
        kind = f.stem
        raw = json.loads(f.read_text(encoding="utf-8"))
        validator = PAYLOAD_VALIDATORS[kind]
        parsed = validator.model_validate(raw)
        assert parsed is not None


async def test_all_fixtures_execute_round_trip(pool: DbPool) -> None:
    """Each fixture payload executes through farm_commands and settles to 'done'."""
    os.environ["CONTRACT_KEY_NEW"] = "test-secret-key"

    alert_id = UUID("a0000000-0000-0000-0000-000000000001")

    # 1. Seed base records needed by the fixtures
    async with pool.connection() as conn:
        # Provider
        await conn.execute(
            """
            insert into public.providers (id, name, kind, executor, default_strategy, enabled)
            values ('p_contract', 'Contract Provider', 'tool', 'api', 'failover', true)
            """
        )
        # Connections
        await conn.execute(
            """
            insert into public.connections (id, provider_id, label, auth_ref, priority, concurrency, status)
            values
                ('c_contract', 'p_contract', 'Base Connection', 'env:CONTRACT_KEY_NEW', 10, 1, 'active'),
                ('c_paused', 'p_contract', 'Paused Connection', 'env:CONTRACT_KEY_NEW', 20, 1, 'paused'),
                ('c_to_remove', 'p_contract', 'To Remove', 'env:CONTRACT_KEY_NEW', 30, 1, 'active')
            """
        )
        # Capability
        await conn.execute(
            """
            insert into public.capabilities (name, kind, description, default_strategy)
            values ('cap_contract', 'tool', 'Contract Capability', 'failover')
            """
        )
        # Alert (unacknowledged)
        await conn.execute(
            """
            insert into public.alerts (id, kind, severity, message, ref, acked_at)
            values (%s, 'quota_warning', 'warn', 'Quota at 80%%', 'c_contract', null)
            """,
            (alert_id,),
        )

    # 2. Execute each fixture
    # Order matters slightly: update/remove after pause/test, add_connection before testing added
    ordered_kinds = [
        "pause",
        "resume",
        "set_priority",
        "set_strategy",
        "set_budget",
        "add_connection",
        "update_connection",
        "test_connection",
        "remove_connection",
        "set_route",
        "ack_alert",
    ]

    for kind in ordered_kinds:
        fixture_path = FIXTURES_DIR / f"{kind}.json"
        assert fixture_path.exists(), f"Fixture file not found: {fixture_path}"

        raw_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cmd_id = uuid4()

        # Queue command in farm_commands
        async with pool.connection() as conn:
            await conn.execute(
                """
                insert into public.farm_commands (id, kind, payload, status, created_by)
                values (%s, %s, %s::jsonb, 'queued', 'console_test')
                """,
                (cmd_id, kind, json.dumps(raw_payload)),
            )

        # Process command
        status, result = await process_command(pool, cmd_id)
        assert status == "done", f"Command '{kind}' rejected/failed: {result.get('error', result)}"

    # 3. Assert resulting database states
    async with pool.connection() as conn:
        # pause: c_contract -> status paused
        cur = await conn.execute("select status from public.connections where id = 'c_contract'")
        row = await cur.fetchone()
        assert row is not None and row[0] == "paused"

        # resume: c_paused -> status active
        cur = await conn.execute("select status from public.connections where id = 'c_paused'")
        row = await cur.fetchone()
        assert row is not None and row[0] == "active"

        # set_priority: c_contract -> priority 25 (was updated by update_connection to 25)
        cur = await conn.execute("select priority from public.connections where id = 'c_contract'")
        row = await cur.fetchone()
        assert row is not None and row[0] == 25

        # set_strategy: p_contract -> default_strategy most_remaining
        cur = await conn.execute("select default_strategy from public.providers where id = 'p_contract'")
        row = await cur.fetchone()
        assert row is not None and row[0] == "most_remaining"

        # set_budget: provider budget ref=p_contract -> monthly_usd 150.0
        cur = await conn.execute(
            "select monthly_usd, hard_stop from public.budgets where scope = 'provider' and ref = 'p_contract'"
        )
        row = await cur.fetchone()
        assert row is not None and float(row[0]) == 150.0 and row[1] is True

        # add_connection: c_contract_added created with credits unit
        cur = await conn.execute(
            "select label, priority, concurrency from public.connections where id = 'c_contract_added'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == "Contract Test Account" and row[1] == 75 and row[2] == 2

        cur = await conn.execute(
            "select limit_value, period, reset_anchor, charged_on from public.consumption_units where connection_id = 'c_contract_added'"
        )
        row = await cur.fetchone()
        assert (
            row is not None
            and float(row[0]) == 5000.0
            and row[1] == "month"
            and row[2] == 15
            and row[3] == "attempt"
        )

        # remove_connection: c_to_remove deleted
        cur = await conn.execute("select id from public.connections where id = 'c_to_remove'")
        assert await cur.fetchone() is None

        # set_route: cap_contract -> p_contract position 1 enabled
        cur = await conn.execute(
            "select position, enabled from public.capability_routes where capability = 'cap_contract' and provider_id = 'p_contract'"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 1 and row[1] is True

        # ack_alert: alert marked acked_at
        cur = await conn.execute("select acked_at from public.alerts where id = %s", (alert_id,))
        row = await cur.fetchone()
        assert row is not None and row[0] is not None
