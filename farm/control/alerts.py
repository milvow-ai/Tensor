"""Alert dispatch and deduplication for Harness Farm.

Dispatches alerts to the database (public.alerts) and Telegram.
Deduplicates identical alerts for 1 hour.
Alert kinds:
- farm_down: farm process or service down / crashed
- needs_login: account needs re-authentication
- account_exhausted: account quota or balance exhausted
- circuit_open_long: circuit breaker open for > 15 minutes
- budget_threshold: spend reached 50%, 80%, or 100% threshold
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog

from farm.db.pool import DbPool
from farm.manager.telegram import is_telegram_configured, send_telegram_alert

__all__ = [
    "ALERT_KINDS",
    "SEVERITIES",
    "ack_alert",
    "is_telegram_configured",
    "list_alerts",
    "send_alert",
]

log = structlog.get_logger(__name__)

SEVERITIES = {"info", "warn", "critical"}

ALERT_KINDS = {
    "farm_down",
    "needs_login",
    "account_exhausted",
    "circuit_open_long",
    "budget_threshold",
}

DEDUP_WINDOW = timedelta(hours=1)


async def send_alert(
    pool: DbPool,
    kind: str,
    message: str,
    severity: str = "warn",
    ref: str | None = None,
    *,
    notify_telegram: bool = True,
    now: datetime | None = None,
) -> UUID:
    """Record an alert in the database and optionally send to Telegram.

    Deduplicates identical alerts (same ref or same kind+message) for 1 hour.
    """
    if severity not in SEVERITIES:
        raise ValueError(f"Invalid severity '{severity}'. Must be one of {SEVERITIES}")

    current = now or datetime.now(UTC)
    dedup_ref = ref or f"{kind}:{message}"
    cutoff = current - DEDUP_WINDOW

    async with pool.connection() as conn:
        # Check for unacknowledged or recent identical alert within deduplication window
        cur = await conn.execute(
            "select id, created_at from public.alerts "
            "where ref = %s and created_at >= %s order by created_at desc limit 1",
            (dedup_ref, cutoff),
        )
        row = await cur.fetchone()
        if row is not None:
            log.info("alert.deduplicated", ref=dedup_ref, alert_id=str(row[0]))
            val = row[0]
            return val if isinstance(val, UUID) else UUID(str(val))

        alert_id = uuid4()
        await conn.execute(
            "insert into public.alerts (id, kind, severity, message, ref, created_at) "
            "values (%s, %s, %s, %s, %s, %s)",
            (alert_id, kind, severity, message, dedup_ref, current),
        )

    log.info("alert.created", alert_id=str(alert_id), kind=kind, severity=severity, ref=dedup_ref)

    if notify_telegram:
        telegram_text = f"<b>[{severity.upper()}] {kind}</b>\n{message}"
        await send_telegram_alert(telegram_text, dedup_key=dedup_ref)

    return alert_id


async def ack_alert(pool: DbPool, alert_id: UUID | str) -> bool:
    """Acknowledge an open alert by its ID."""
    aid = alert_id if isinstance(alert_id, UUID) else UUID(str(alert_id))
    async with pool.connection() as conn:
        cur = await conn.execute(
            "update public.alerts set acked_at = now() where id = %s and acked_at is null",
            (aid,),
        )
        return cur.rowcount > 0


async def list_alerts(pool: DbPool, limit: int = 50, only_unacked: bool = True) -> list[dict[str, Any]]:
    """List alerts ordered newest first."""
    query = (
        "select id, kind, severity, message, ref, created_at, acked_at "
        "from public.alerts "
        + ("where acked_at is null " if only_unacked else "")
        + "order by created_at desc limit %s"
    )
    async with pool.connection() as conn:
        cur = await conn.execute(query, (limit,))
        rows = await cur.fetchall()
        return [
            {
                "id": row[0],
                "kind": row[1],
                "severity": row[2],
                "message": row[3],
                "ref": row[4],
                "created_at": row[5],
                "acked_at": row[6],
            }
            for row in rows
        ]
