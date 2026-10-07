"""Integration requests: sessions request missing capabilities (GUIDE1b).

AI sessions in any project can record missing integrations (MCP servers, CLIs, APIs, accounts).
Stores rows in public.integration_requests:
- id, created_at, requested_by, name, kind, purpose, context, urgency, links, status, owner_note, resolved_at.
- Dedupes identical open requests (same name + kind) by adding the new purpose to the existing row.
- Rejects secret-shaped strings using looks_like_secret.
- Dispatches alerts to the owner (kind: integration_request).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from farm.control.alerts import send_alert
from farm.db.pool import DbPool
from farm.registry.models import looks_like_secret

log = structlog.get_logger(__name__)

RequestKind = Literal["mcp", "cli", "api", "account", "other"]
RequestUrgency = Literal["now", "soon", "later"]
RequestStatus = Literal["open", "in_progress", "done", "declined"]

VALID_KINDS: set[str] = {"mcp", "cli", "api", "account", "other"}
VALID_URGENCIES: set[str] = {"now", "soon", "later"}
VALID_STATUSES: set[str] = {"open", "in_progress", "done", "declined"}
_COLUMNS: str = (
    "id, created_at, requested_by, name, kind, purpose, "
    "context, urgency, links, status, owner_note, resolved_at"
)


class IntegrationRequest(BaseModel):
    """An integration requested by an AI session or user."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    created_at: datetime
    requested_by: str
    name: str
    kind: RequestKind
    purpose: str
    context: str | None = None
    urgency: RequestUrgency = "soon"
    links: list[str] = Field(default_factory=list)
    status: RequestStatus = "open"
    owner_note: str | None = None
    resolved_at: datetime | None = None


def _check_secrets(name: str, purpose: str, task_context: str | None, links: list[str] | None) -> None:
    for field_name, value in [("name", name), ("purpose", purpose), ("task_context", task_context)]:
        if value and looks_like_secret(value):
            raise ValueError(
                f"Integration request field '{field_name}' contains text shaped like a secret key or "
                "credential. Never submit secrets in integration requests."
            )
    if links:
        for link in links:
            if link and looks_like_secret(link):
                raise ValueError(
                    "Integration request link contains text shaped like a secret or credential."
                )


async def request_integration(
    pool: DbPool,
    name: str,
    kind: str,
    purpose: str,
    task_context: str | None = None,
    urgency: str = "soon",
    links: list[str] | None = None,
    requested_by: str = "unknown",
    now: datetime | None = None,
    notify_telegram: bool = True,
) -> tuple[IntegrationRequest, bool]:
    """Record an integration request.

    Dedupes identical open requests (same name + kind) by updating the existing row's purpose.
    Returns (request, deduplicated).
    """
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Integration request name cannot be empty")
    clean_kind = kind.strip().lower()
    if clean_kind not in VALID_KINDS:
        raise ValueError(f"Invalid integration kind '{kind}'. Must be one of {sorted(VALID_KINDS)}")
    clean_purpose = purpose.strip()
    if not clean_purpose:
        raise ValueError("Integration request purpose cannot be empty")
    clean_urgency = urgency.strip().lower()
    if clean_urgency not in VALID_URGENCIES:
        raise ValueError(f"Invalid urgency '{urgency}'. Must be one of {sorted(VALID_URGENCIES)}")

    clean_links = [link.strip() for link in (links or []) if link.strip()]

    _check_secrets(clean_name, clean_purpose, task_context, clean_links)

    current_time = now or datetime.now(UTC)

    async with pool.connection() as conn:
        # Check for existing open request with same name + kind
        cur = await conn.execute(
            f"""
            select {_COLUMNS}
            from public.integration_requests
            where status = 'open' and lower(name) = lower(%s) and kind = %s
            order by created_at desc
            limit 1
            for update
            """,
            (clean_name, clean_kind),
        )
        row = await cur.fetchone()

        if row is not None:
            # Deduplicate by appending new purpose to existing row
            existing_id = row[0]
            existing_purpose = row[5]
            existing_context = row[6]
            existing_urgency = row[7]
            existing_links = list(row[8] or [])

            if clean_purpose in existing_purpose:
                updated_purpose = existing_purpose
            else:
                updated_purpose = f"{existing_purpose}\n\nAdditional request: {clean_purpose}"

            updated_context = existing_context or task_context
            merged_links = sorted(set(existing_links) | set(clean_links))
            updated_urgency = (
                "now" if (clean_urgency == "now" or existing_urgency == "now") else existing_urgency
            )

            up_cur = await conn.execute(
                f"""
                update public.integration_requests
                set purpose = %s, context = %s, urgency = %s, links = %s
                where id = %s
                returning {_COLUMNS}
                """,
                (updated_purpose, updated_context, updated_urgency, merged_links, existing_id),
            )
            up_row = await up_cur.fetchone()
            assert up_row is not None
            req = IntegrationRequest(
                id=up_row[0],
                created_at=up_row[1],
                requested_by=up_row[2],
                name=up_row[3],
                kind=up_row[4],
                purpose=up_row[5],
                context=up_row[6],
                urgency=up_row[7],
                links=up_row[8] or [],
                status=up_row[9],
                owner_note=up_row[10],
                resolved_at=up_row[11],
            )
            dedup = True
        else:
            new_id = uuid4()
            ins_cur = await conn.execute(
                f"""
                insert into public.integration_requests (
                  id, created_at, requested_by, name, kind, purpose, context, urgency, links, status
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open'
                )
                returning {_COLUMNS}
                """,
                (
                    new_id,
                    current_time,
                    requested_by,
                    clean_name,
                    clean_kind,
                    clean_purpose,
                    task_context,
                    clean_urgency,
                    clean_links,
                ),
            )
            ins_row = await ins_cur.fetchone()
            assert ins_row is not None
            req = IntegrationRequest(
                id=ins_row[0],
                created_at=ins_row[1],
                requested_by=ins_row[2],
                name=ins_row[3],
                kind=ins_row[4],
                purpose=ins_row[5],
                context=ins_row[6],
                urgency=ins_row[7],
                links=ins_row[8] or [],
                status=ins_row[9],
                owner_note=ins_row[10],
                resolved_at=ins_row[11],
            )
            dedup = False

    # Alert the owner
    alert_severity = "warn" if clean_urgency == "now" else "info"
    alert_msg = f"Integration request ({clean_kind}): {clean_name} — {clean_purpose}"
    alert_ref = f"integration_request:{clean_name.lower()}:{clean_kind}"
    try:
        await send_alert(
            pool,
            kind="integration_request",
            message=alert_msg,
            severity=alert_severity,
            ref=alert_ref,
            now=current_time,
            notify_telegram=notify_telegram,
        )
    except Exception as exc:
        log.warning("integration_request.alert_failed", error=str(exc))

    return req, dedup


async def list_integration_requests(
    pool: DbPool,
    status: str | None = None,
    limit: int = 100,
) -> list[IntegrationRequest]:
    """List integration requests, newest first."""
    query = f"""
        select {_COLUMNS}
        from public.integration_requests
    """
    params: list[Any] = []
    if status is not None and status.strip():
        s_clean = status.strip().lower()
        if s_clean not in VALID_STATUSES:
            raise ValueError(f"Invalid status filter '{status}'. Must be one of {sorted(VALID_STATUSES)}")
        query += " where status = %s"
        params.append(s_clean)
    query += " order by created_at desc limit %s"
    params.append(limit)

    async with pool.connection() as conn:
        cur = await conn.execute(query, tuple(params))
        rows = await cur.fetchall()

    return [
        IntegrationRequest(
            id=row[0],
            created_at=row[1],
            requested_by=row[2],
            name=row[3],
            kind=row[4],
            purpose=row[5],
            context=row[6],
            urgency=row[7],
            links=row[8] or [],
            status=row[9],
            owner_note=row[10],
            resolved_at=row[11],
        )
        for row in rows
    ]


async def get_integration_request(
    pool: DbPool,
    request_id: UUID | str,
) -> IntegrationRequest | None:
    """Fetch an integration request by ID."""
    rid = request_id if isinstance(request_id, UUID) else UUID(str(request_id))
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""
            select {_COLUMNS}
            from public.integration_requests
            where id = %s
            """,
            (rid,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return IntegrationRequest(
            id=row[0],
            created_at=row[1],
            requested_by=row[2],
            name=row[3],
            kind=row[4],
            purpose=row[5],
            context=row[6],
            urgency=row[7],
            links=row[8] or [],
            status=row[9],
            owner_note=row[10],
            resolved_at=row[11],
        )


async def resolve_integration_request(
    pool: DbPool,
    request_id: UUID | str,
    status: str = "done",
    owner_note: str | None = None,
    now: datetime | None = None,
) -> IntegrationRequest:
    """Resolve an integration request with a status (in_progress, done, declined) and optional note."""
    rid = request_id if isinstance(request_id, UUID) else UUID(str(request_id))
    clean_status = status.strip().lower()
    if clean_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {sorted(VALID_STATUSES)}")

    current_time = now or datetime.now(UTC)
    resolved_at = current_time if clean_status in ("done", "declined") else None

    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""
            update public.integration_requests
            set status = %s,
                owner_note = coalesce(%s, owner_note),
                resolved_at = coalesce(%s, resolved_at)
            where id = %s
            returning {_COLUMNS}
            """,
            (clean_status, owner_note, resolved_at, rid),
        )
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"Integration request '{request_id}' not found")

        return IntegrationRequest(
            id=row[0],
            created_at=row[1],
            requested_by=row[2],
            name=row[3],
            kind=row[4],
            purpose=row[5],
            context=row[6],
            urgency=row[7],
            links=row[8] or [],
            status=row[9],
            owner_note=row[10],
            resolved_at=row[11],
        )


async def execute_resolve_integration_request(
    pool: DbPool,
    payload: dict[str, Any],
    actor: str = "system",
) -> tuple[str, dict[str, Any]]:
    """Execute resolve_integration_request command (from farm_commands or CLI)."""
    req_id_raw = payload.get("request_id")
    if not req_id_raw:
        return "rejected", {"error": "Missing 'request_id'"}
    status = payload.get("status", "done")
    owner_note = payload.get("owner_note")

    try:
        req = await resolve_integration_request(
            pool,
            request_id=req_id_raw,
            status=status,
            owner_note=owner_note,
        )
        return "done", {
            "request_id": str(req.id),
            "status": req.status,
            "owner_note": req.owner_note,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
        }
    except Exception as exc:
        log.exception("integration_request.resolve_failed", request_id=str(req_id_raw), error=str(exc))
        return "failed", {"error": str(exc)}
