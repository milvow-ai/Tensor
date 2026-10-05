"""Tests for Console SQL views (0003_console_views.py and console/sql/views.sql).

Seeds providers, connections, quota usage, connection health, ai sessions,
usage/billing events, capabilities, routes, and runs with the `pool` fixture,
and asserts key aggregate numbers across all 5 views:
  - v_connection_status
  - v_pool_overview
  - v_spend_month
  - v_capability_capacity
  - v_recent_runs
"""

from decimal import Decimal
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from alembic import command

from farm.control.cli import alembic_config
from farm.db.pool import DbPool


@pytest.mark.asyncio
async def test_console_views_key_numbers(pool: DbPool) -> None:
    async with pool.connection() as conn:
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
                ('c_circuit_open', 'p_tool', 'Broken Account', 'env:BROKEN_KEY', 20, 1, 'active', '{"price_usd": 50.0, "name": "Basic"}'::jsonb, '{}'::jsonb),
                ('c_paused', 'p_tool', 'Paused Account', 'env:PAUSED_KEY', 30, 1, 'paused', '{"price_usd": 0.0}'::jsonb, '{}'::jsonb),
                ('c_ai_1', 'p_ai', 'AI Account 1', 'cli:claude-01', 100, 1, 'active', '{"price_usd": 20.0}'::jsonb, '{"cli": "claude"}'::jsonb)
            """
        )

        # 3. Seed consumption units
        await conn.execute(
            """
            insert into public.consumption_units
                (connection_id, unit, limit_value, period, reset_anchor, charged_on, unit_cost_usd, estimate_per_call)
            values
                ('c_active', 'credits', 1000, 'month', 1, 'attempt', 0.01, 2),
                ('c_circuit_open', 'credits', 500, 'month', 1, 'attempt', 0.02, 1),
                ('c_paused', 'credits', 200, 'month', 1, 'attempt', 0.05, 1)
            """
        )

        # 4. Seed quota usage for c_active (used=300, reserved=100 => remaining=600, calls_remaining=300)
        cur = await conn.execute("select farm_period_start('month', 1, now())")
        row = await cur.fetchone()
        assert row is not None
        p_start = row[0]

        await conn.execute(
            """
            insert into public.quota_usage (connection_id, unit, period_start, used, reserved, limit_value)
            values ('c_active', 'credits', %s, 300, 100, 1000)
            """,
            (p_start,),
        )

        # 5. Seed connection health (c_circuit_open has circuit='open')
        await conn.execute(
            """
            insert into public.connection_health (connection_id, circuit, consecutive_failures, success_count, failure_count)
            values
                ('c_active', 'closed', 0, 50, 1),
                ('c_circuit_open', 'open', 5, 10, 5)
            """
        )

        # 6. Seed AI sessions for c_ai_1
        await conn.execute(
            """
            insert into public.ai_sessions (session_id, connection_id, ai, model, created_at, last_used_at)
            values
                ('sess-1', 'c_ai_1', 'claude', 'claude-3-7-sonnet', now(), now()),
                ('sess-2', 'c_ai_1', 'claude', 'claude-3-7-sonnet', now(), now())
            """
        )

        # 7. Seed capability, route and requests before runs
        await conn.execute(
            """
            insert into public.capabilities (name, kind, description, default_strategy)
            values ('verify_email', 'tool', 'Verify email deliverability', 'failover')
            """
        )
        await conn.execute(
            """
            insert into public.capability_routes (capability, provider_id, position, enabled)
            values ('verify_email', 'p_tool', 1, true)
            """
        )

        req_id = uuid4()
        run_1 = uuid4()
        run_2 = uuid4()
        await conn.execute(
            """
            insert into public.capability_requests (id, request_hash, capability, params, status)
            values (%s, 'hash-contract', 'verify_email', '{}'::jsonb, 'succeeded')
            """,
            (req_id,),
        )
        await conn.execute(
            """
            insert into public.runs (id, capability, request_id, caller, strategy, status, cost_usd, cached, connection_id, started_at, finished_at)
            values
                (%s, 'verify_email', %s, 'console', 'failover', 'succeeded', 0.02, false, 'c_active', now() - interval '10 seconds', now()),
                (%s, 'verify_email', %s, 'console', 'failover', 'succeeded', 0.02, false, 'c_active', now() - interval '5 seconds', now())
            """,
            (run_1, req_id, run_2, req_id),
        )

        # Run events for run_1: 2 execute attempts
        await conn.execute(
            """
            insert into public.run_events (run_id, seq, kind, connection_id, data, at)
            values
                (%s, 1, 'execute', 'c_circuit_open', '{}'::jsonb, now() - interval '8 seconds'),
                (%s, 2, 'execute', 'c_active', '{}'::jsonb, now() - interval '4 seconds')
            """,
            (run_1, run_1),
        )

        # 8. Seed spend (usage_events and billing_events)
        await conn.execute(
            """
            insert into public.usage_events (connection_id, unit, amount, kind, cost_usd, at)
            values
                ('c_active', 'credits', 10, 'actual', 0.10, now()),
                ('c_active', 'credits', 20, 'actual', 0.20, now())
            """
        )
        await conn.execute(
            """
            insert into public.billing_events (connection_id, kind, amount_usd, at, note)
            values
                ('c_active', 'charge', 120.00, now(), 'Monthly renewal'),
                ('c_active', 'refund', 20.00, now(), 'Partial credit')
            """
        )

        # 9. Seed budgets and settings
        await conn.execute(
            """
            insert into public.budgets (scope, ref, monthly_usd, hard_stop)
            values
                ('provider', 'p_tool', 250.00, true),
                ('global', null, 800.00, true)
            """
        )
        await conn.execute("update public.farm_settings set global_monthly_budget_usd = 800.00 where id = 1")

    # =========================================================================
    # ASSERT VIEW 1: v_connection_status
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select connection_id, status, effective_state, plan_price_usd, calls_today,
                   remaining, calls_remaining, sessions_count
            from public.v_connection_status
            order by connection_id
            """
        )
        rows = {r[0]: r for r in await cur.fetchall()}

        # c_active: active status, circuit closed => effective_state active
        c_act = rows["c_active"]
        assert c_act[1] == "active"
        assert c_act[2] == "active"
        assert Decimal(str(c_act[3])) == Decimal("120.0")
        assert c_act[4] == 2  # 2 runs started today
        assert Decimal(str(c_act[5])) == Decimal("600")  # remaining = 1000 - 300 - 100
        assert Decimal(str(c_act[6])) == Decimal("300")  # floor(600 / 2)

        # c_circuit_open: active status, but health circuit open => effective_state circuit_open
        c_open = rows["c_circuit_open"]
        assert c_open[1] == "active"
        assert c_open[2] == "circuit_open"

        # c_paused: paused status => effective_state paused
        c_p = rows["c_paused"]
        assert c_p[1] == "paused"
        assert c_p[2] == "paused"

        # c_ai_1: sessions_count == 2
        c_ai = rows["c_ai_1"]
        assert c_ai[7] == 2

    # =========================================================================
    # ASSERT VIEW 2: v_pool_overview
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select provider_id, accounts_total, accounts_active, accounts_paused,
                   accounts_open_circuit, accounts_usable, remaining_calls, monthly_plan_usd, health
            from public.v_pool_overview
            where provider_id = 'p_tool'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[1] == 3  # total accounts
        assert row[2] == 2  # active status (c_active, c_circuit_open)
        assert row[3] == 1  # paused (c_paused)
        assert row[4] == 1  # open circuit (c_circuit_open)
        assert row[5] == 1  # usable = effective_state active (c_active only)
        assert Decimal(str(row[6])) == Decimal("300")  # remaining calls of usable accounts
        assert Decimal(str(row[7])) == Decimal("170.0")  # monthly plan sum: 120 + 50 + 0
        assert row[8] == "degraded"  # degraded because circuit_open exists

    # =========================================================================
    # ASSERT VIEW 3: v_spend_month
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select provider_id, usage_usd, billing_usd, spend_usd, budget_usd
            from public.v_spend_month
            where provider_id in ('p_tool', 'total')
            order by provider_id
            """
        )
        spend = {r[0]: r for r in await cur.fetchall()}

        p_row = spend["p_tool"]
        # usage = 0.10 + 0.20 = 0.30; billing = 120 - 20 = 100 => spend = 100.30
        assert Decimal(str(p_row[1])) == Decimal("0.30")
        assert Decimal(str(p_row[2])) == Decimal("100.00")
        assert Decimal(str(p_row[3])) == Decimal("100.30")
        assert Decimal(str(p_row[4])) == Decimal("250.00")

        tot_row = spend["total"]
        assert Decimal(str(tot_row[1])) == Decimal("0.30")
        assert Decimal(str(tot_row[2])) == Decimal("100.00")
        assert Decimal(str(tot_row[3])) == Decimal("100.30")
        assert Decimal(str(tot_row[4])) == Decimal("800.00")

    # =========================================================================
    # ASSERT VIEW 4: v_capability_capacity
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select capability, provider_id, route_position, route_enabled,
                   health, accounts_usable, accounts_total, remaining_calls
            from public.v_capability_capacity
            where capability = 'verify_email'
            """
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "verify_email"
        assert row[1] == "p_tool"
        assert row[2] == 1
        assert row[3] is True
        assert row[4] == "degraded"
        assert row[5] == 1
        assert row[6] == 3
        assert Decimal(str(row[7])) == Decimal("300")

    # =========================================================================
    # ASSERT VIEW 5: v_recent_runs
    # =========================================================================
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select id, capability, connection_id, connection_label, provider_name,
                   cost_usd, attempts_count
            from public.v_recent_runs
            where id = %s
            """,
            (run_1,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[1] == "verify_email"
        assert row[2] == "c_active"
        assert row[3] == "Active Account"
        assert row[4] == "Tool Pool"
        assert Decimal(str(row[5])) == Decimal("0.02")
        assert row[6] == 2  # 2 execute run_events


def test_console_views_migration_downgrade_and_upgrade(scratch_db: Any) -> None:
    """Test that 0003_console_views upgrades cleanly, downgrades by dropping the views, and re-upgrades."""
    url = scratch_db()
    cfg = alembic_config(url)

    # 1. Upgrade to 0003 (later migrations add more views)
    command.upgrade(cfg, "0003")
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "select count(*) from pg_views where schemaname = 'public' and viewname like 'v_%'"
        ).fetchone()
        assert row is not None and row[0] == 5

    # 2. Downgrade to 0002
    command.downgrade(cfg, "0002")
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "select count(*) from pg_views where schemaname = 'public' and viewname like 'v_%'"
        ).fetchone()
        assert row is not None and row[0] == 0

    # 3. Upgrade back to 0003
    command.upgrade(cfg, "0003")
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "select count(*) from pg_views where schemaname = 'public' and viewname like 'v_%'"
        ).fetchone()
        assert row is not None and row[0] == 5


def test_c2_views_migration_downgrade_and_upgrade(scratch_db: Any) -> None:
    """0006_c2_views adds the five Billing/Runs views on top of 0003's five; below 0006 only 0003's remain."""
    url = scratch_db()
    cfg = alembic_config(url)
    c2_views = {"v_spend_daily", "v_cost_per_result", "v_renewals", "v_idle_paid", "v_run_detail"}

    def views() -> set[str]:
        with psycopg.connect(url) as conn:
            rows = conn.execute("select viewname from pg_views where schemaname = 'public'").fetchall()
        return {r[0] for r in rows if r[0].startswith("v_")}

    command.upgrade(cfg, "head")
    at_head = views()
    assert c2_views <= at_head

    command.downgrade(cfg, "0005")
    after = views()
    assert not (c2_views & after) and len(after) == 5  # only the five 0003 views remain below 0006

    command.upgrade(cfg, "head")
    assert views() == at_head
