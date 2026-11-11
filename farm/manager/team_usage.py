"""Explicitly enabled, bounded usage counters. No prompts, results, labels or credentials."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from farm.db.pool import DbPool, tenant_connection
from farm.manager.ai_usage import calendar_bounds


async def snapshot(pool: DbPool, now: datetime, instance: str) -> dict[str, Any]:
    async with tenant_connection(pool) as conn:
        cursor = await conn.execute("select timezone from farm_settings where id=1")
        row = await cursor.fetchone()
        timezone = str(row[0]) if row and row[0] else "UTC"
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            timezone = "UTC"
        start = calendar_bounds(now, timezone)["month_start"]
        cursor = await conn.execute("""
            select count(c.id) filter(where p.kind='ai'),
                   count(c.id) filter(where p.executor='mcp'),
                   count(c.id) filter(where p.kind<>'ai' and p.executor<>'mcp')
              from providers p left join connections c on c.provider_id=p.id
        """)
        counts = await cursor.fetchone()
        assert counts is not None
        cursor = await conn.execute(
            """
            select state,count(*) from ai_jobs
             where coalesce(finished_at,created_at)>=%s or state in ('running','queued') group by state
        """,
            (start,),
        )
        states = {str(r[0]): int(r[1]) for r in await cursor.fetchall()}
        cursor = await conn.execute(
            """
            select count(*) filter(where j.cost_estimated),
                   count(*) filter(where not exists(select 1 from usage_events e
                       where e.run_id=j.run_id and e.kind='actual'))
              from ai_jobs j where j.state in ('succeeded','failed','cancelled')
               and j.finished_at >= %s and j.finished_at <= %s
        """,
            (start, now),
        )
        missing = await cursor.fetchone()
        assert missing is not None
        cursor = await conn.execute(
            """
            select coalesce(sum(cost_usd),0) from usage_events where kind='actual' and at>=%s and at<=%s
        """,
            (start, now),
        )
        cost = await cursor.fetchone()
        cursor = await conn.execute(
            """
            select c.provider_id,e.unit,sum(e.amount) from usage_events e
              join connections c on c.id=e.connection_id
             where e.kind='actual' and e.at>=%s and e.at<=%s
             group by c.provider_id,e.unit order by c.provider_id,e.unit limit 201
        """,
            (start, now),
        )
        units = await cursor.fetchall()
    return {
        "version": 1,
        "instance": instance,
        "observed_at": now.isoformat(),
        "timezone": timezone,
        "window_start": start.isoformat(),
        "ai_accounts": counts[0],
        "mcp_accounts": counts[1],
        "other_accounts": counts[2],
        "jobs": states,
        "unpriced_jobs": missing[0],
        "missing_metering_jobs": missing[1],
        "recorded_cost_usd": float(cost[0]) if cost else 0,
        "usage": [{"provider": r[0], "unit": r[1], "amount": float(r[2])} for r in units[:200]],
        "usage_truncated": len(units) > 200,
    }
