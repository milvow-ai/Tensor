from datetime import UTC, datetime

from farm.manager.ai_usage import calendar_bounds


def test_calendar_bounds_use_tenant_timezone_and_month_edges() -> None:
    now = datetime(2026, 3, 1, 1, 30, tzinfo=UTC)
    bounds = calendar_bounds(now, "America/Los_Angeles")
    assert bounds["day_start"] == datetime(2026, 2, 28, 8, 0, tzinfo=UTC)
    assert bounds["day_end"] == datetime(2026, 3, 1, 8, 0, tzinfo=UTC)
    assert bounds["month_start"] == datetime(2026, 2, 1, 8, 0, tzinfo=UTC)


def test_invalid_timezone_falls_back_to_utc() -> None:
    now = datetime(2026, 3, 1, 1, 30, tzinfo=UTC)
    assert calendar_bounds(now, "not/a-zone")["day_start"] == datetime(2026, 3, 1, tzinfo=UTC)
    assert calendar_bounds(now, "../x")["day_start"] == datetime(2026, 3, 1, tzinfo=UTC)
    assert calendar_bounds(now, "/etc/passwd")["day_start"] == datetime(2026, 3, 1, tzinfo=UTC)


async def test_snapshot_rls_and_error_preserve_last_success(pool, two_tenants, monkeypatch) -> None:
    from datetime import timedelta

    from psycopg.types.json import Jsonb

    from farm.db.pool import tenant_connection
    from farm.manager import ai_usage
    from farm.tenancy import use_tenant
    from tests.conftest import seed_connection

    a_conn = str(two_tenants.params[two_tenants.a]["conn"])
    b_conn = str(two_tenants.params[two_tenants.b]["conn"])
    for tenant, conn_id in ((two_tenants.a, a_conn), (two_tenants.b, b_conn)):
        with use_tenant(tenant):
            async with tenant_connection(pool) as conn:
                await conn.execute(
                    "insert into public.ai_usage_snapshots(connection_id,provider_id,status,observed_at,checked_at,snapshot) "
                    "values(%s,'codex','ok',now()-interval '1 hour',now()-interval '1 hour',%s) "
                    "on conflict(tenant_id,connection_id) do update set provider_id=excluded.provider_id, "
                    "status=excluded.status, observed_at=excluded.observed_at, "
                    "checked_at=excluded.checked_at, snapshot=excluded.snapshot",
                    (conn_id, Jsonb({"rate_limits": {"primary": {"usedPercent": 15}}})),
                )
                cur = await conn.execute(
                    "select count(*) from public.ai_usage_snapshots where connection_id=%s",
                    (b_conn if tenant == two_tenants.a else a_conn,),
                )
                assert await cur.fetchone() == (0,)

    target = "codex-monitor-test"
    await seed_connection(
        pool,
        "codex",
        target,
        kind="ai",
        executor="cli_agent",
        status="active",
        meta={"home": "/does/not/matter"},
    )
    async with tenant_connection(pool) as conn:
        now = datetime.now(UTC)
        await conn.execute(
            "insert into public.ai_usage_snapshots(connection_id,provider_id,status,observed_at,checked_at,snapshot) "
            "values(%s,'codex','ok',%s,%s,%s)",
            (
                target,
                now - timedelta(minutes=5),
                now - timedelta(minutes=20),
                Jsonb({"rate_limits": {"primary": {"usedPercent": 42}}}),
            ),
        )

    async def failed_probe(_meta):
        return {"status": "error", "error_code": "timeout"}

    monkeypatch.setattr("farm.manager.codex_usage.probe_codex", failed_probe)
    await ai_usage.poll_provider_allowances(pool, datetime.now(UTC))
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select status,error_code,observed_at,snapshot from public.ai_usage_snapshots where connection_id=%s",
            (target,),
        )
        status, code, observed, snapshot = await cur.fetchone()
    assert (status, code) == ("error", "timeout")
    assert observed is not None
    assert snapshot == {"rate_limits": {"primary": {"usedPercent": 42}}}
    report = await ai_usage.usage_report(pool, datetime.now(UTC))
    allowance = next(item for item in report["provider_allowances"] if item["connection_id"] == target)
    assert allowance["freshness"] == "stale"


async def test_paused_account_is_not_probed(pool, monkeypatch) -> None:
    from farm.db.pool import tenant_connection
    from farm.manager.ai_usage import poll_provider_allowances
    from tests.conftest import seed_connection

    await seed_connection(
        pool,
        "codex",
        "paused-codex",
        kind="ai",
        executor="cli_agent",
        status="paused",
        meta={"home": "/unused"},
    )

    async def unexpected(_meta):
        raise AssertionError("paused account was probed")

    monkeypatch.setattr("farm.manager.codex_usage.probe_codex", unexpected)
    assert await poll_provider_allowances(pool, datetime.now(UTC)) == {
        "checked": 0,
        "ok": 0,
        "errors": 0,
        "skipped": 0,
    }
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select count(*) from public.ai_usage_snapshots where connection_id='paused-codex'"
        )
        assert await cur.fetchone() == (0,)


async def test_actual_only_usage_and_failed_job_unknown_metering(pool) -> None:
    from datetime import timedelta

    from farm.db.pool import tenant_connection
    from farm.manager.ai_usage import usage_report
    from tests.conftest import seed_connection

    account = "metered-codex"
    await seed_connection(pool, "metered", account, kind="ai", executor="cli_agent")
    now = datetime.now(UTC)
    async with tenant_connection(pool) as conn:
        await conn.execute("update public.farm_settings set timezone='UTC' where id=1")
        await conn.execute(
            "insert into public.usage_events(connection_id,unit,amount,kind,cost_usd,at) values "
            "(%s,'input_tokens',123,'actual',0,%s),(%s,'input_tokens',999,'estimated',4,%s),"
            "(%s,'input_tokens',7,'actual',2,%s)",
            (account, now, account, now, account, now + timedelta(seconds=1)),
        )
        await conn.execute(
            "insert into public.ai_conversations(id,ai,account) values(gen_random_uuid(),'codex',%s)",
            (account,),
        )
        await conn.execute(
            "insert into public.ai_jobs(conversation_id,turn,ai,account,state,error_kind,finished_at) "
            "select id,1,'codex',%s,'failed','crash',%s from public.ai_conversations where account=%s",
            (account, now, account),
        )
    report = await usage_report(pool, now + timedelta(microseconds=1))
    entry = next(
        x for x in report["observed_usage"] if x["connection_id"] == account and x["unit"] == "input_tokens"
    )
    assert entry["day_amount"] == 123
    assert entry["month_amount"] == 123
    assert entry["day_zero_or_unknown_cost_events"] == 1
    assert report["failed_jobs_without_metered_events_month_to_date"] == 1
    assert entry["day_jobs_without_metered_events"] == 1
    assert entry["month_jobs_without_metered_events"] == 1


async def test_empty_ai_account_is_reported_as_unknown(pool) -> None:
    from farm.manager.ai_usage import usage_report
    from tests.conftest import seed_connection

    await seed_connection(pool, "empty-metering", "empty-ai-account", kind="ai", executor="cli_agent")
    report = await usage_report(pool, datetime.now(UTC))
    entry = next(item for item in report["observed_usage"] if item["connection_id"] == "empty-ai-account")
    assert entry["unit"] is None
    assert entry["day_events"] == 0
    assert entry["month_events"] == 0
    assert entry["metering"] == "unknown"


async def test_snapshot_foreign_key_rejects_another_tenant_connection(pool, two_tenants) -> None:
    import pytest
    from psycopg.errors import ForeignKeyViolation

    from farm.db.pool import tenant_connection
    from farm.tenancy import use_tenant

    other_connection = str(two_tenants.params[two_tenants.b]["conn"])
    with use_tenant(two_tenants.a):
        async with tenant_connection(pool) as conn:
            with pytest.raises(ForeignKeyViolation):
                await conn.execute(
                    "insert into public.ai_usage_snapshots(connection_id,provider_id,status) values(%s,'codex','error')",
                    (other_connection,),
                )


async def test_probe_failure_does_not_poison_other_scheduler_maintenance(monkeypatch) -> None:
    from farm.manager import scheduler

    expired = False

    async def failed_probe(_pool, _now):
        raise RuntimeError("monitor unavailable")

    async def expire(_pool):
        nonlocal expired
        expired = True
        return []

    async def each(_pool, work, *, task):
        try:
            return [await work()]
        except Exception:
            return []

    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(scheduler, "poll_provider_allowances", failed_probe)
    monkeypatch.setattr(scheduler, "for_each_tenant", each)
    monkeypatch.setattr(scheduler.ledger, "expire", expire)
    monkeypatch.setattr(scheduler, "reactivate_due_connections", empty)
    monkeypatch.setattr(scheduler, "check_budget_thresholds", empty)
    monkeypatch.setattr(scheduler, "check_renewal_reminders", empty)
    result = await scheduler.run_maintenance_tick(object(), include_usage=True)
    assert expired
    assert result["ai_usage_probes"] == []


async def test_probe_upsert_and_multibucket_freshness(pool, monkeypatch) -> None:
    from datetime import timedelta

    from farm.db.pool import tenant_connection
    from farm.manager.ai_usage import poll_provider_allowances, usage_report
    from tests.conftest import seed_connection

    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    await seed_connection(pool, "codex", "probe-ok", kind="ai", executor="cli_agent", meta={"home": "/fake"})
    calls = []

    async def probe(meta):
        calls.append(meta)
        return {
            "status": "ok",
            "rate_limits": {
                "rateLimitsByLimitId": {
                    "codex": {
                        "primary": {"usedPercent": 12, "resetsAt": (now + timedelta(minutes=15)).timestamp()}
                    }
                }
            },
        }

    monkeypatch.setattr("farm.manager.codex_usage.probe_codex", probe)
    assert (await poll_provider_allowances(pool, now))["ok"] == 1
    assert (await poll_provider_allowances(pool, now + timedelta(seconds=1)))["checked"] == 0
    assert (await poll_provider_allowances(pool, now + timedelta(minutes=10)))["ok"] == 1
    assert len(calls) == 2
    report = await usage_report(pool, now + timedelta(minutes=16))
    allowance = next(a for a in report["provider_allowances"] if a["connection_id"] == "probe-ok")
    assert allowance["freshness"] == "stale"  # observed six minutes ago, but nested window reset
    assert (
        allowance["snapshot"]["rate_limits"]["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] == 12
    )
    async with tenant_connection(pool) as conn:
        await conn.execute("update public.providers set enabled=false where id='codex'")
    assert (await poll_provider_allowances(pool, now + timedelta(hours=1)))["checked"] == 0
    assert len(calls) == 2


async def test_missing_and_future_observations_are_unknown(pool) -> None:
    from datetime import timedelta

    from farm.db.pool import tenant_connection
    from farm.manager.ai_usage import usage_report
    from tests.conftest import seed_connection

    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    await seed_connection(pool, "codex", "no-observation", kind="ai", executor="cli_agent")
    report = await usage_report(pool, now)
    allowance = next(a for a in report["provider_allowances"] if a["connection_id"] == "no-observation")
    assert allowance["freshness"] == "unknown" and allowance["snapshot"] is None
    async with tenant_connection(pool) as conn:
        await conn.execute(
            "insert into public.ai_usage_snapshots(connection_id,provider_id,status,observed_at) "
            "values('no-observation','codex','ok',%s)",
            (now + timedelta(days=1),),
        )
        await conn.execute(
            "insert into public.usage_events(connection_id,unit,amount,kind,cost_usd,at) "
            "values('no-observation','input_tokens',999,'actual',1,%s)",
            (now + timedelta(days=1),),
        )
    report = await usage_report(pool, now)
    assert report["provider_allowances"][0]["freshness"] == "unknown"
    entry = next(a for a in report["observed_usage"] if a["connection_id"] == "no-observation")
    assert entry["month_events"] == 0 and entry["month_amount"] == 0


async def test_request_ids_are_lower_bound_with_unidentified_event_count(pool) -> None:
    from datetime import timedelta

    from farm.db.pool import tenant_connection
    from farm.manager.ai_usage import usage_report
    from tests.conftest import seed_connection

    now = datetime.now(UTC)
    request_id = "00000000-0000-0000-0000-000000000001"
    await seed_connection(pool, "partial-meter", "partial-ai", kind="ai", executor="cli_agent")
    async with tenant_connection(pool) as conn:
        await conn.execute(
            "insert into public.usage_events(connection_id,unit,amount,kind,request_id,at) values "
            "('partial-ai','input_tokens',1,'actual',%s,%s),"
            "('partial-ai','output_tokens',2,'actual',%s,%s),"
            "('partial-ai','input_tokens',3,'actual',null,%s)",
            (request_id, now, request_id, now, now),
        )
    report = await usage_report(pool, now + timedelta(microseconds=1))
    calls = next(item for item in report["account_call_counts"] if item["connection_id"] == "partial-ai")
    assert calls["month_calls"] == 1 and calls["month_unidentified_events"] == 1
    assert calls["status"] == "partial"


async def test_concurrent_probe_batches_skip_and_cancel_releases_lock(pool, monkeypatch) -> None:
    import asyncio

    from farm.manager.ai_usage import poll_provider_allowances
    from tests.conftest import seed_connection

    await seed_connection(
        pool, "codex", "locked-probe", kind="ai", executor="cli_agent", meta={"home": "/fake"}
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def blocked_probe(_meta):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"status": "ok", "rate_limits": {}}

    monkeypatch.setattr("farm.manager.codex_usage.probe_codex", blocked_probe)
    first = asyncio.create_task(poll_provider_allowances(pool, datetime.now(UTC)))
    await asyncio.wait_for(entered.wait(), timeout=5)
    second = await poll_provider_allowances(pool, datetime.now(UTC))
    assert second == {"checked": 0, "ok": 0, "errors": 0, "skipped": 1}
    first.cancel()
    import pytest

    with pytest.raises(asyncio.CancelledError):
        await first

    async def successful_probe(_meta):
        nonlocal calls
        calls += 1
        return {"status": "ok", "rate_limits": {}}

    monkeypatch.setattr("farm.manager.codex_usage.probe_codex", successful_probe)
    third = await poll_provider_allowances(pool, datetime.now(UTC))
    assert third["ok"] == 1 and calls == 2


async def test_busy_account_requires_three_intervals_before_probe(pool, monkeypatch) -> None:
    from datetime import timedelta

    from farm.db.pool import tenant_connection
    from farm.manager.ai_usage import poll_provider_allowances
    from tests.conftest import seed_connection

    now = datetime.now(UTC)
    await seed_connection(
        pool, "codex", "busy-probe", kind="ai", executor="cli_agent", meta={"home": "/fake"}
    )
    async with tenant_connection(pool) as conn:
        await conn.execute(
            "insert into public.ai_usage_settings(id,poll_interval_seconds) values(1,60) on conflict(tenant_id,id) do update set poll_interval_seconds=60"
        )
        await conn.execute(
            "insert into public.ai_conversations(id,ai,account) values(gen_random_uuid(),'codex','busy-probe')"
        )
        await conn.execute(
            "insert into public.ai_jobs(conversation_id,turn,ai,account,state,started_at) "
            "select id,1,'codex','busy-probe','running',%s from public.ai_conversations where account='busy-probe'",
            (now - timedelta(minutes=4),),
        )
        await conn.execute(
            "insert into public.ai_usage_snapshots(connection_id,provider_id,status,observed_at,checked_at,snapshot) "
            "values('busy-probe','codex','ok',%s,%s,'{}'::jsonb)",
            (now - timedelta(minutes=4), now - timedelta(minutes=4)),
        )

    calls = 0

    async def probe(_meta):
        nonlocal calls
        calls += 1
        return {"status": "ok", "rate_limits": {}}

    monkeypatch.setattr("farm.manager.codex_usage.probe_codex", probe)
    assert (await poll_provider_allowances(pool, now))["ok"] == 1
    assert calls == 1
    async with tenant_connection(pool) as conn:
        await conn.execute("update public.ai_jobs set started_at=%s where account='busy-probe'", (now,))
        await conn.execute(
            "update public.ai_usage_snapshots set checked_at=%s where connection_id='busy-probe'", (now,)
        )
    assert (await poll_provider_allowances(pool, now + timedelta(seconds=30)))["checked"] == 0
    assert calls == 1
    async with tenant_connection(pool) as conn:
        await conn.execute("delete from public.ai_usage_snapshots where connection_id='busy-probe'")
        await conn.execute(
            "update public.ai_jobs set started_at=%s where account='busy-probe'",
            (now - timedelta(minutes=4),),
        )
    assert (await poll_provider_allowances(pool, now + timedelta(minutes=4)))["ok"] == 1
    assert calls == 2


async def test_usage_tick_keeps_probe_reports_and_is_opt_in(monkeypatch) -> None:
    from farm.manager import scheduler

    calls = []
    report = {"checked": 2, "ok": 1, "errors": 1, "skipped": 0}

    async def probe(_pool, _now):
        calls.append(True)
        return report

    async def each(_pool, work, *, task):
        return [await work()]

    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(scheduler, "for_each_tenant", each)
    monkeypatch.setattr(scheduler, "poll_provider_allowances", probe)
    monkeypatch.setattr(scheduler.ledger, "expire", empty)
    for name in ("reactivate_due_connections", "check_budget_thresholds", "check_renewal_reminders"):
        monkeypatch.setattr(scheduler, name, empty)
    assert (await scheduler.run_maintenance_tick(object()))["ai_usage_probes"] == []
    assert calls == []
    assert (await scheduler.run_maintenance_tick(object(), include_usage=True))["ai_usage_probes"] == [report]
    assert calls == [True]


async def test_scheduler_usage_does_not_block_maintenance_and_stop_cancels_probe(monkeypatch) -> None:
    import asyncio

    from farm.manager import scheduler

    entered = asyncio.Event()
    maintained = asyncio.Event()
    cancelled = asyncio.Event()

    async def probe(_pool, _now):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def each(_pool, work, *, task):
        return [await work()]

    async def maintenance(_pool):
        await entered.wait()
        maintained.set()
        return []

    async def empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr(scheduler, "for_each_tenant", each)
    monkeypatch.setattr(scheduler, "poll_provider_allowances", probe)
    monkeypatch.setattr(scheduler.ledger, "expire", maintenance)
    monkeypatch.setattr(scheduler, "reactivate_due_connections", empty)
    manager = scheduler.ManagerScheduler(object())
    await manager.start()
    try:
        await asyncio.wait_for(maintained.wait(), 3)
    finally:
        await asyncio.wait_for(manager.stop(), 3)
    assert cancelled.is_set()
    assert manager._task is None and manager._usage_task is None
