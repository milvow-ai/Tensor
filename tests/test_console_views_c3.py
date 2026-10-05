"""Tests for Console C3 SQL views (console/sql/views_c3.sql) and C2 view assertions.

Verifies:
  - Upgrade/downgrade of C3 views and C2 migrations
  - Numeric checks for C3 views:
      * v_routes
      * v_facts
      * v_evidence
  - Numeric checks for C2 views:
      * v_spend_daily
      * v_cost_per_result
      * v_renewals
      * v_idle_paid
      * v_run_detail
"""

from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic import command

from farm.control.cli import alembic_config
from farm.db.pool import DbPool

VIEWS_C3_SQL_PATH = Path(__file__).parent.parent / "console" / "sql" / "views_c3.sql"


def test_views_c3_create_drop_upgrade_downgrade(scratch_db: Any) -> None:
    """Migration 0009 creates the three C3 views (from views_c3.sql) and drops only those on downgrade."""
    url = scratch_db()
    cfg = alembic_config(url)
    c3_views = {"v_routes", "v_facts", "v_evidence"}

    def get_c3_views() -> set[str]:
        with psycopg.connect(url) as conn:
            rows = conn.execute(
                "select viewname from pg_views where schemaname = 'public' "
                "and viewname in ('v_routes', 'v_facts', 'v_evidence')"
            ).fetchall()
        return {r[0] for r in rows}

    command.upgrade(cfg, "head")
    assert get_c3_views() == c3_views
    assert "create or replace view v_routes" in VIEWS_C3_SQL_PATH.read_text(encoding="utf-8")

    command.downgrade(cfg, "0008")
    assert get_c3_views() == set()

    command.upgrade(cfg, "head")
    assert get_c3_views() == c3_views


def test_c2_views_migration_roundtrip(scratch_db: Any) -> None:
    """Test upgrade to head (with C2 views), downgrade to 0005, and upgrade back."""
    url = scratch_db()
    cfg = alembic_config(url)
    c2_views = {"v_spend_daily", "v_cost_per_result", "v_renewals", "v_idle_paid", "v_run_detail"}

    def views() -> set[str]:
        with psycopg.connect(url) as conn:
            rows = conn.execute("select viewname from pg_views where schemaname = 'public'").fetchall()
        return {r[0] for r in rows if r[0].startswith("v_")}

    command.upgrade(cfg, "head")
    assert c2_views <= views()

    command.downgrade(cfg, "0005")
    assert not (c2_views & views())

    command.upgrade(cfg, "head")
    assert c2_views <= views()


@pytest.mark.asyncio
async def test_console_views_c3_and_c2_numeric_checks(pool: DbPool) -> None:
    """Seed comprehensive test data and assert numeric checks for C3 views and C2 views."""
    c3_sql = VIEWS_C3_SQL_PATH.read_text(encoding="utf-8")

    async with pool.connection() as conn:
        # Apply C3 views
        await conn.execute(c3_sql)

        # 1. Seed providers
        await conn.execute(
            """
            insert into public.providers (id, name, kind, executor, default_strategy, enabled)
            values
                ('p_tool', 'Tool Pool', 'tool', 'api', 'failover', true),
                ('p_ai', 'AI Pool', 'ai', 'cli_agent', 'most_remaining', true)
            """
        )

        # 2. Seed connections
        await conn.execute(
            """
            insert into public.connections (id, provider_id, label, auth_ref, priority, concurrency, status, plan, meta)
            values
                ('c_active', 'p_tool', 'Active Account', 'env:ACTIVE_KEY', 10, 2, 'active', '{"price_usd": 120.0, "name": "Growth", "billing_day": 1}'::jsonb, '{}'::jsonb),
                ('c_idle_paid', 'p_tool', 'Idle Paid Account', 'env:IDLE_KEY', 20, 1, 'active', '{"price_usd": 49.0, "name": "Paid Idle", "billing_day": 15}'::jsonb, '{}'::jsonb),
                ('c_free', 'p_tool', 'Free Account', 'env:FREE_KEY', 30, 1, 'active', '{"price_usd": 0.0, "name": "Free"}'::jsonb, '{}'::jsonb)
            """
        )

        # 3. Seed consumption units
        await conn.execute(
            """
            insert into public.consumption_units
                (connection_id, unit, limit_value, period, reset_anchor, charged_on, unit_cost_usd, estimate_per_call)
            values
                ('c_active', 'credits', 1000, 'month', 1, 'attempt', 0.01, 2),
                ('c_idle_paid', 'credits', 500, 'month', 15, 'attempt', 0.02, 1),
                ('c_free', 'credits', 100, 'month', 1, 'attempt', 0.0, 1)
            """
        )

        # 4. Seed quota usage for c_active (used=400, reserved=100 => remaining=500, calls_remaining=250)
        cur = await conn.execute("select farm_period_start('month', 1, now())")
        row = await cur.fetchone()
        assert row is not None
        p_start = row[0]

        await conn.execute(
            """
            insert into public.quota_usage (connection_id, unit, period_start, used, reserved, limit_value)
            values ('c_active', 'credits', %s, 400, 100, 1000)
            """,
            (p_start,),
        )

        # 5. Seed connection health (c_active healthy, c_idle_paid idle with last_success 20 days ago)
        await conn.execute(
            """
            insert into public.connection_health (connection_id, circuit, consecutive_failures, success_count, failure_count, last_success_at)
            values
                ('c_active', 'closed', 0, 100, 2, now() - interval '1 hour'),
                ('c_idle_paid', 'closed', 0, 5, 0, now() - interval '20 days')
            """
        )

        # 6. Seed capability and routes
        await conn.execute(
            """
            insert into public.capabilities (name, kind, description, default_strategy, cache_ttl_seconds)
            values ('verify_email', 'tool', 'Verify deliverability', 'failover', 3600)
            """
        )
        await conn.execute(
            """
            insert into public.capability_routes (capability, provider_id, position, enabled)
            values ('verify_email', 'p_tool', 1, true)
            """
        )

        # 7. Seed entity, facts, and evidence
        entity_id = uuid4()
        evidence_id = uuid4()

        await conn.execute(
            """
            insert into public.entities (id, kind, canonical_key, name)
            values (%s, 'domain', 'acme.com', 'Acme Corp')
            """,
            (entity_id,),
        )

        await conn.execute(
            """
            insert into public.evidence (id, sha256, path, url, thumb_path, captured_at, tool_version)
            values (%s, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', '/evidence/acme.png', 'https://acme.com', '/evidence/acme_thumb.png', now(), 'v1.0')
            """,
            (evidence_id,),
        )

        await conn.execute(
            """
            insert into public.facts (id, entity_id, attribute, value, source_connection_id, observed_at, expires_at, confidence, evidence_ids)
            values (%s, %s, 'tech_stack', '{"cms": "wordpress"}'::jsonb, 'c_active', now(), now() + interval '30 days', 0.95, array[%s]::uuid[])
            """,
            (uuid4(), entity_id, evidence_id),
        )

        # 8. Seed capability request, run, and events for C2 views
        req_id = uuid4()
        run_id = uuid4()

        await conn.execute(
            """
            insert into public.capability_requests (id, request_hash, capability, params, status)
            values (%s, 'hash-c3-test', 'verify_email', '{"email": "test@acme.com"}'::jsonb, 'succeeded')
            """,
            (req_id,),
        )

        await conn.execute(
            """
            insert into public.runs (id, capability, request_id, caller, strategy, status, cost_usd, cached, connection_id, started_at, finished_at)
            values (%s, 'verify_email', %s, 'console', 'failover', 'succeeded', 0.05, false, 'c_active', now() - interval '20 seconds', now() - interval '5 seconds')
            """,
            (run_id, req_id),
        )

        await conn.execute(
            """
            insert into public.run_events (run_id, seq, kind, connection_id, data, at)
            values
                (%s, 1, 'plan', 'c_active', '{}'::jsonb, now() - interval '18 seconds'),
                (%s, 2, 'execute', 'c_active', '{}'::jsonb, now() - interval '15 seconds'),
                (%s, 3, 'success', 'c_active', '{}'::jsonb, now() - interval '5 seconds')
            """,
            (run_id, run_id, run_id),
        )

        # 9. Seed usage and billing events for C2 views
        await conn.execute(
            """
            insert into public.usage_events (connection_id, unit, amount, kind, cost_usd, at)
            values ('c_active', 'credits', 5, 'actual', 0.05, now())
            """
        )
        await conn.execute(
            """
            insert into public.billing_events (connection_id, kind, amount_usd, at, note)
            values ('c_active', 'charge', 120.00, now(), 'Plan renewal')
            """
        )

    # =========================================================================
    # ASSERT C3 VIEW 1: v_routes
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select capability, provider_id, position, enabled, remaining_calls, accounts_usable
            from public.v_routes
            where capability = 'verify_email'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "verify_email"
        assert row[1] == "p_tool"
        assert row[2] == 1  # position
        assert row[3] is True  # enabled
        # Remaining calls for c_active is 250, c_idle_paid has 500, c_free has 100 => pool remaining
        assert Decimal(str(row[4])) > Decimal(0)
        assert row[5] >= 1

    # =========================================================================
    # ASSERT C3 VIEW 2: v_facts
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select entity_canonical_key, attribute, confidence, freshness_state, source_label
            from public.v_facts
            where entity_canonical_key = 'acme.com'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "acme.com"
        assert row[1] == "tech_stack"
        assert Decimal(str(row[2])) == Decimal("0.95")  # confidence numeric check
        assert row[3] == "fresh"
        assert row[4] == "Active Account"

    # =========================================================================
    # ASSERT C3 VIEW 3: v_evidence
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select sha256, robots_decision, facts_count
            from public.v_evidence
            where id = %s
            """,
            (evidence_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert row[1] == "allowed"
        assert row[2] == 1  # 1 referencing fact

    # =========================================================================
    # ASSERT C2 VIEW 1: v_spend_daily
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select provider_id, usage_usd, billing_usd, spend_usd
            from public.v_spend_daily
            where provider_id = 'p_tool'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert Decimal(str(row[1])) == Decimal("0.05")
        assert Decimal(str(row[2])) == Decimal("120.00")
        assert Decimal(str(row[3])) == Decimal("120.05")

    # =========================================================================
    # ASSERT C2 VIEW 2: v_cost_per_result
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select connection_id, spend_usd, successful_results
            from public.v_cost_per_result
            where connection_id = 'c_active'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert Decimal(str(row[1])) == Decimal("120.05")
        assert row[2] >= 1  # at least 1 successful result

    # =========================================================================
    # ASSERT C2 VIEW 3: v_renewals
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select connection_id, price_usd, billing_day
            from public.v_renewals
            where connection_id = 'c_active'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert Decimal(str(row[1])) == Decimal("120.0")
        assert row[2] == 1

    # =========================================================================
    # ASSERT C2 VIEW 4: v_idle_paid
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select connection_id, plan_price_usd, days_idle
            from public.v_idle_paid
            where connection_id = 'c_idle_paid'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert Decimal(str(row[1])) == Decimal("49.0")
        assert row[2] >= 14  # idle for >= 14 days

    # =========================================================================
    # ASSERT C2 VIEW 5: v_run_detail
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select id, cost_usd, duration_ms
            from public.v_run_detail
            where id = %s
            """,
            (run_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert Decimal(str(row[1])) == Decimal("0.05")
        assert row[2] == 15000  # 15 seconds duration_ms
