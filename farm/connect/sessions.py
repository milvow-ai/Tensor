"""Login sessions: what the dashboard shows while one account signs in (``public.login_sessions``).

A session holds links, device codes and states, never a secret. Every function acts for the current
tenant (``tenant_connection``) except the two sweeps, which span tenants on purpose
(``system_connection``). A finished session is never changed again: every update requires
``finished_at is null``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from farm.db.pool import Conn, DbPool, system_connection, tenant_connection

ACTIVE_STATES: tuple[str, ...] = ("queued", "starting", "waiting_for_user", "verifying")
FINISHED_STATES: tuple[str, ...] = (
    "done",
    "failed",
    "expired",
    "cancelled",
    "duplicate",
    "needs_verification",
)
_UPDATABLE = frozenset(
    {
        "auth_url",
        "user_code",
        "verification_uri",
        "needs_input",
        "input_hint",
        "expires_at",
        "identity",
        "evidence",
        "error_kind",
        "error_excerpt",
    }
)
_JSON_COLUMNS = frozenset({"identity", "evidence"})
RESTART_MESSAGE = "The Farm restarted during this sign-in. Start it again."

type Session = dict[str, Any]


async def create(pool: DbPool, *, connection_id: str, provider_id: str, method: str, created_by: str) -> UUID:
    """A new ``queued`` session; raises ``psycopg.errors.UniqueViolation`` when one is already active."""
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "insert into public.login_sessions (connection_id, provider_id, method, created_by) "
            "values (%s, %s, %s, %s) returning id",
            (connection_id, provider_id, method, created_by),
        )
        row = await cur.fetchone()
    if row is None:  # pragma: no cover - an insert ... returning always returns the row
        raise RuntimeError("login session insert returned nothing")
    return UUID(str(row[0]))


async def get(pool: DbPool, session_id: UUID) -> Session | None:
    async with tenant_connection(pool) as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute("select * from public.login_sessions where id = %s", (session_id,))
        return await cur.fetchone()


async def active_for(pool: DbPool, connection_id: str) -> Session | None:
    """The session that is still running for this connection, if any (there is at most one)."""
    async with tenant_connection(pool) as conn:
        cur = conn.cursor(row_factory=dict_row)
        await cur.execute(
            "select * from public.login_sessions where connection_id = %s and state = any(%s) "
            "order by created_at desc limit 1",
            (connection_id, list(ACTIVE_STATES)),
        )
        return await cur.fetchone()


async def update_in(conn: Conn, session_id: UUID, *, state: str | None = None, **fields: Any) -> bool:
    """Change an unfinished session on ``conn``; returns False when it is unknown or already finished.

    A state in ``FINISHED_STATES`` also sets ``finished_at`` (``updated_at`` is kept by a trigger).
    """
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"login_sessions has no updatable column(s) {sorted(unknown)}")
    assignments: list[sql.Composable] = []
    values: list[Any] = []
    if state is not None:
        assignments.append(sql.SQL("state = %s"))
        values.append(state)
        if state in FINISHED_STATES:
            assignments.append(sql.SQL("finished_at = now()"))
    for name, value in fields.items():
        assignments.append(sql.SQL("{} = %s").format(sql.Identifier(name)))
        values.append(Jsonb(value) if name in _JSON_COLUMNS else value)
    if not assignments:
        return False
    query = sql.SQL("update public.login_sessions set {} where id = %s and finished_at is null").format(
        sql.SQL(", ").join(assignments)
    )
    cur = await conn.execute(query, (*values, session_id))
    return cur.rowcount > 0


async def update(pool: DbPool, session_id: UUID, *, state: str | None = None, **fields: Any) -> bool:
    async with tenant_connection(pool) as conn:
        return await update_in(conn, session_id, state=state, **fields)


async def expire_overdue(pool: DbPool) -> int:
    """Every tenant: sessions still waiting for a person after ``expires_at`` become ``expired``."""
    async with system_connection(pool) as conn:
        cur = await conn.execute(
            "update public.login_sessions set state = 'expired', error_kind = 'expired', "
            "error_excerpt = 'Nobody finished this sign-in in time. Start it again.', finished_at = now() "
            "where state = 'waiting_for_user' and expires_at < now() and finished_at is null"
        )
        return cur.rowcount


async def fail_active_after_restart(pool: DbPool) -> int:
    """Every tenant: sessions a previous Farm process left running can never finish; mark them failed."""
    async with system_connection(pool) as conn:
        cur = await conn.execute(
            "update public.login_sessions set state = 'failed', error_kind = 'farm_restart', "
            "error_excerpt = %s, finished_at = now() where state = any(%s) and finished_at is null",
            (RESTART_MESSAGE, list(ACTIVE_STATES)),
        )
        return cur.rowcount
