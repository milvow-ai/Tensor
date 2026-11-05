"""Farm-observed AI usage windows and provider quota observations."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from farm.db.pool import DbPool, tenant_connection


def calendar_bounds(now: datetime, timezone: str) -> dict[str, datetime]:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
        timezone = "UTC"
    local = now.astimezone(zone)
    day = local.date()
    month = day.replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {
        "day_start": datetime.combine(day, time.min, zone).astimezone(now.tzinfo),
        "day_end": datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(now.tzinfo),
        "month_start": datetime.combine(month, time.min, zone).astimezone(now.tzinfo),
        "month_end": datetime.combine(next_month, time.min, zone).astimezone(now.tzinfo),
    }


async def usage_report(pool: DbPool, now: datetime) -> dict[str, Any]:
    async with tenant_connection(pool) as conn:
        cur = await conn.execute("select timezone from public.farm_settings where id=1")
        row = await cur.fetchone()
        configured_timezone = str(row[0]) if row and row[0] else "UTC"
        bounds = calendar_bounds(now, configured_timezone)
        applied_timezone = configured_timezone
        try:
            ZoneInfo(configured_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            applied_timezone = "UTC"

        cur = await conn.execute(
            """select count(*),count(*) filter(where j.state in ('failed','cancelled'))
              from public.ai_jobs j where j.state in ('succeeded','failed','cancelled')
              and j.finished_at >= %s and j.finished_at < %s
              and (j.run_id is null or not exists (
                select 1 from public.usage_events e where e.run_id=j.run_id and e.kind='actual'))""",
            (bounds["month_start"], now),
        )
        missing_row = await cur.fetchone()
        assert missing_row is not None  # an aggregate without GROUP BY returns one row
        missing, failed_missing = missing_row
        cur = await conn.execute(
            """select c.id,c.provider_id,c.label,e.unit,
              sum(e.amount) filter(where e.at >= %s and e.at < %s),
              sum(e.amount) filter(where e.at >= %s and e.at < %s),
              sum(e.cost_usd) filter(where e.at >= %s and e.at < %s),
              sum(e.cost_usd) filter(where e.at >= %s and e.at < %s),
              count(*) filter(where e.at >= %s and e.at < %s),
              count(*) filter(where e.at >= %s and e.at < %s),
              count(*) filter(where e.at >= %s and e.at < %s and e.cost_usd > 0),
              count(*) filter(where e.at >= %s and e.at < %s and e.cost_usd = 0),
              count(*) filter(where e.at >= %s and e.at < %s and e.cost_usd > 0),
              count(*) filter(where e.at >= %s and e.at < %s and e.cost_usd = 0)
              from public.connections c join public.providers p on p.id=c.provider_id
              left join public.usage_events e on e.connection_id=c.id and e.kind='actual'
                and e.at >= %s and e.at < %s
              where p.kind='ai' group by c.id,c.provider_id,c.label,e.unit
              order by c.provider_id,c.id,e.unit""",
            (
                bounds["day_start"],
                now,
                bounds["month_start"],
                now,
                bounds["day_start"],
                now,
                bounds["month_start"],
                now,
                bounds["day_start"],
                now,
                bounds["month_start"],
                now,
                bounds["day_start"],
                now,
                bounds["day_start"],
                now,
                bounds["month_start"],
                now,
                bounds["month_start"],
                now,
                bounds["month_start"],
                now,
            ),
        )
        account_rows = await cur.fetchall()
        cur = await conn.execute(
            """select e.connection_id,
              count(distinct e.request_id) filter(where e.at >= %s and e.at < %s),
              count(distinct e.request_id) filter(where e.at >= %s and e.at < %s),
              count(*) filter(where e.request_id is null and e.at >= %s and e.at < %s),
              count(*) filter(where e.request_id is null and e.at >= %s and e.at < %s)
              from public.usage_events e join public.connections c on c.id=e.connection_id
              join public.providers p on p.id=c.provider_id
              where p.kind='ai' and e.kind='actual' and e.at >= %s and e.at < %s
              group by e.connection_id""",
            (
                bounds["day_start"],
                now,
                bounds["month_start"],
                now,
                bounds["day_start"],
                now,
                bounds["month_start"],
                now,
                bounds["month_start"],
                now,
            ),
        )
        call_counts = {row[0]: (row[1], row[2], row[3], row[4]) for row in await cur.fetchall()}
        cur = await conn.execute(
            """select j.account,
              count(*) filter(where j.finished_at >= %s and j.finished_at < %s),
              count(*) filter(where j.finished_at >= %s and j.finished_at < %s)
              from public.ai_jobs j
              where j.state in ('succeeded','failed','cancelled')
                and j.finished_at >= %s and j.finished_at < %s
                and (j.run_id is null or not exists (
                  select 1 from public.usage_events e
                  where e.run_id=j.run_id and e.kind='actual'))
              group by j.account""",
            (bounds["day_start"], now, bounds["month_start"], now, bounds["month_start"], now),
        )
        job_missing = {row[0]: (row[1], row[2]) for row in await cur.fetchall()}
        cur = await conn.execute(
            "select connection_id,provider_id,status,error_code,observed_at,checked_at,snapshot "
            "from public.ai_usage_snapshots order by provider_id,connection_id"
        )
        snapshots = await cur.fetchall()
        cur = await conn.execute("select poll_interval_seconds from public.ai_usage_settings where id=1")
        setting = await cur.fetchone()
        poll_interval = int(setting[0]) if setting else 600
    allowances = []
    snapshot_by_account = {snapshot[0]: snapshot for snapshot in snapshots}
    seen_accounts = set()
    for account_id, provider_id, _label, _unit, *_rest in account_rows:
        if account_id in seen_accounts:
            continue
        seen_accounts.add(account_id)
        if account_id not in snapshot_by_account:
            allowances.append(
                {
                    "connection_id": account_id,
                    "provider_id": provider_id,
                    "status": "unknown",
                    "freshness": "unknown",
                    "error_code": None,
                    "observed_at": None,
                    "checked_at": None,
                    "snapshot": None,
                }
            )
    for snapshot in snapshots:
        observed = snapshot[4]
        age = (now - observed).total_seconds() if observed else float("inf")
        expired_window = False
        stack = [(snapshot[6] or {}, 0)]
        while stack:
            value, depth = stack.pop()
            if not isinstance(value, dict) or depth > 5:
                continue
            reset_at = value.get("resetsAt")
            if isinstance(reset_at, (int, float)) and reset_at <= now.timestamp():
                expired_window = True
            if depth < 5:
                stack.extend((child, depth + 1) for child in value.values() if isinstance(child, dict))
        if age < 0:
            freshness = "unknown"
        else:
            freshness = (
                "stale" if snapshot[2] != "ok" or expired_window or age > 2 * poll_interval else "current"
            )
        allowances.append(
            {
                "connection_id": snapshot[0],
                "provider_id": snapshot[1],
                "status": snapshot[2],
                "freshness": "unknown" if observed is None else freshness,
                "error_code": snapshot[3],
                "observed_at": observed.isoformat() if observed else None,
                "checked_at": snapshot[5].isoformat() if snapshot[5] else None,
                "snapshot": snapshot[6],
            }
        )
    usage = [
        {
            "connection_id": r[0],
            "provider_id": r[1],
            "label": r[2],
            "unit": r[3],
            "day_calls": call_counts.get(r[0], (0, 0, 0, 0))[0],
            "month_calls": call_counts.get(r[0], (0, 0, 0, 0))[1],
            "day_unidentified_events": call_counts.get(r[0], (0, 0, 0, 0))[2],
            "month_unidentified_events": call_counts.get(r[0], (0, 0, 0, 0))[3],
            "call_count_status": "partial"
            if call_counts.get(r[0], (0, 0, 0, 0))[3]
            else ("observed" if call_counts.get(r[0], (0, 0, 0, 0))[1] else "unknown"),
            "day_amount": float(r[4] or 0),
            "month_amount": float(r[5] or 0),
            "day_known_cost_usd": float(r[6] or 0),
            "month_known_cost_usd": float(r[7] or 0),
            "day_events": r[8] or 0,
            "month_events": r[9] or 0,
            "day_cost_known_events": r[10] or 0,
            "day_zero_or_unknown_cost_events": r[11] or 0,
            "month_cost_known_events": r[12] or 0,
            "month_zero_or_unknown_cost_events": r[13] or 0,
            "metering": "observed" if (r[9] or 0) else "unknown",
            "day_jobs_without_metered_events": job_missing.get(r[0], (0, 0))[0],
            "month_jobs_without_metered_events": job_missing.get(r[0], (0, 0))[1],
        }
        for r in account_rows
    ]
    account_call_counts = []
    for account_id in seen_accounts:
        counts = call_counts.get(account_id, (0, 0, 0, 0))
        account_call_counts.append(
            {
                "connection_id": account_id,
                "day_calls": counts[0],
                "month_calls": counts[1],
                "day_unidentified_events": counts[2],
                "month_unidentified_events": counts[3],
                "status": "partial" if counts[3] else ("observed" if counts[1] else "unknown"),
            }
        )
    return {
        "timezone": applied_timezone,
        "boundaries": {key: value.isoformat() for key, value in bounds.items()},
        "observed_usage": usage,
        "account_call_counts": account_call_counts,
        "jobs_without_metered_events_month_to_date": missing,
        "failed_jobs_without_metered_events_month_to_date": failed_missing,
        "provider_allowances": allowances,
    }


async def poll_provider_allowances(pool: DbPool, now: datetime, *, batch_size: int = 4) -> dict[str, int]:
    """Take a tenant-scoped DB advisory lock for the duration of the probe batch."""
    async with tenant_connection(pool) as lock_conn, lock_conn.transaction():
        cur = await lock_conn.execute(
            "select pg_try_advisory_xact_lock(hashtextextended(current_setting('app.tenant_id'), 184733))"
        )
        lock_row = await cur.fetchone()
        if not lock_row or not lock_row[0]:
            return {"checked": 0, "ok": 0, "errors": 0, "skipped": 1}
        else:
            return await _poll_provider_allowances_locked(pool, now, batch_size=batch_size)


async def _poll_provider_allowances_locked(
    pool: DbPool, now: datetime, *, batch_size: int = 4
) -> dict[str, int]:
    """Probe a bounded serial batch ordered by oldest due check."""
    from psycopg.types.json import Jsonb

    from farm.manager.codex_usage import probe_codex

    batch_size = max(1, min(int(batch_size), 32))
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            """select c.id,c.provider_id,c.meta
              from public.connections c join public.providers p on p.id=c.provider_id
              where p.kind='ai' and p.enabled and c.status='active'
              and (not exists(select 1 from public.ai_jobs j
                where j.account=c.id and j.state in ('queued','running'))
                or (select min(coalesce(j.started_at,j.created_at)) from public.ai_jobs j
                  where j.account=c.id and j.state in ('queued','running')) < %s - make_interval(secs => 3 *
                    least(86400,greatest(60,coalesce((select poll_interval_seconds
                      from public.ai_usage_settings where id=1),600)))))
              and not exists(select 1 from public.ai_usage_snapshots s where s.connection_id=c.id
                and s.checked_at > %s - make_interval(secs =>
                  least(86400, greatest(60, coalesce((select poll_interval_seconds
                    from public.ai_usage_settings where id=1),600))) *
                  case when s.status='ok' then 1 else least(6,power(2,least(s.error_streak,3))::int) end))
              order by (select min(s.checked_at) from public.ai_usage_snapshots s
                where s.connection_id=c.id) nulls first,
                c.id limit %s""",
            (now, now, batch_size),
        )
        rows = await cur.fetchall()
    result = {"checked": 0, "ok": 0, "errors": 0, "skipped": 0}
    for connection_id, provider_id, meta in rows:
        result["checked"] += 1
        if str(provider_id).lower() not in {"codex", "openai-codex"}:
            outcome = {"status": "unsupported", "error_code": "provider_quota_unsupported"}
        else:
            try:
                outcome = await probe_codex(meta or {})
            except Exception:
                outcome = {"status": "error", "error_code": "probe_failed"}
        async with tenant_connection(pool) as conn:
            if outcome.get("status") == "ok":
                await conn.execute(
                    """insert into public.ai_usage_snapshots
                      (connection_id,provider_id,status,error_code,error_streak,observed_at,checked_at,snapshot)
                      values(%s,%s,'ok',null,0,%s,%s,%s)
                      on conflict(tenant_id,connection_id) do update set
                      provider_id=excluded.provider_id,status='ok',
                      error_code=null,error_streak=0,observed_at=excluded.observed_at,
                      checked_at=excluded.checked_at,snapshot=excluded.snapshot""",
                    (connection_id, provider_id, now, now, Jsonb(outcome)),
                )
                result["ok"] += 1
            else:
                await conn.execute(
                    """insert into public.ai_usage_snapshots
                      (connection_id,provider_id,status,error_code,checked_at,snapshot,error_streak)
                      values(%s,%s,%s,%s,%s,'{}'::jsonb,case when %s='unsupported' then 0 else 1 end)
                      on conflict(tenant_id,connection_id) do update set
                      status=excluded.status,error_code=excluded.error_code,
                      error_streak=case when excluded.status='unsupported' then 0
                      else least(2147483647::bigint,ai_usage_snapshots.error_streak::bigint+1)::int end,
                      checked_at=excluded.checked_at""",
                    (
                        connection_id,
                        provider_id,
                        outcome.get("status", "error"),
                        outcome.get("error_code", "probe_failed"),
                        now,
                        outcome.get("status"),
                    ),
                )
                result["errors"] += 1
    return result
