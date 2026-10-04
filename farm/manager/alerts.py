"""Alert management and dispatch for Harness Farm.

Persists alerts to the database and dispatches notifications via Telegram when configured.
Handles alert deduplication, acknowledgment, and querying.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import structlog

from farm.db.pool import DbPool
from farm.manager.telegram import send_telegram_alert

log = structlog.get_logger(__name__)

SEVERITIES = {"info", "warn", "critical"}


async def create_alert(
    pool: DbPool,
    kind: str,
    severity: str,
    message: str,
    ref: str | None = None,
    *,
    notify_telegram: bool = True,
    deduplicate: bool = True,
) -> UUID:
    """Create an alert in the database and optionally send to Telegram.

    If deduplicate is True and an unacknowledged alert with the same ref exists, returns
    the existing alert ID without duplicating the alert or notification.
    """
    if severity not in SEVERITIES:
        raise ValueError(f"Invalid severity '{severity}'. Must be one of {SEVERITIES}")

    async with pool.connection() as conn:
        if deduplicate and ref is not None:
            cur = await conn.execute(
                "select id from public.alerts where ref = %s and acked_at is null limit 1",
                (ref,),
            )
            row = await cur.fetchone()
            if row is not None:
                log.info("alert.deduplicated", ref=ref, alert_id=str(row[0]))
                val = row[0]
                return val if isinstance(val, UUID) else UUID(str(val))

        alert_id = uuid4()
        await conn.execute(
            "insert into public.alerts (id, kind, severity, message, ref) values (%s, %s, %s, %s, %s)",
            (alert_id, kind, severity, message, ref),
        )

    log.info("alert.created", alert_id=str(alert_id), kind=kind, severity=severity, ref=ref)

    if notify_telegram:
        telegram_text = f"<b>[{severity.upper()}] {kind}</b>\n{message}"
        await send_telegram_alert(telegram_text, dedup_key=ref or str(alert_id))

    return alert_id


async def ack_alert(pool: DbPool, alert_id: UUID | str) -> bool:
    """Acknowledge an open alert by its ID. Returns True if an unacknowledged alert was updated."""
    aid = alert_id if isinstance(alert_id, UUID) else UUID(str(alert_id))
    async with pool.connection() as conn:
        cur = await conn.execute(
            "update public.alerts set acked_at = now() where id = %s and acked_at is null",
            (aid,),
        )
        return cur.rowcount > 0


async def list_open_alerts(pool: DbPool, limit: int = 50) -> list[dict[str, Any]]:
    """List unacknowledged alerts ordered newest first."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select id, kind, severity, message, ref, created_at "
            "from public.alerts where acked_at is null order by created_at desc limit %s",
            (limit,),
        )
        rows = await cur.fetchall()
        return [
            {
                "id": row[0],
                "kind": row[1],
                "severity": row[2],
                "message": row[3],
                "ref": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]
