"""Conversations: one thread with one worker, kept on the account that holds its CLI session.

A conversation is created by the first ``ai_start`` and continued by ``ai_reply`` (or by ``ai_start``
with its id).
It names the account (the session lives there: a conversation never moves to another account once it has a
native session), the CLI-native session id, and the running totals of its turns. Only the trajectory is kept,
never what was said.

The helpers taking a ``conn`` run inside the transaction of the job update that causes them, so a turn is
counted exactly once, together with the job that finished it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel

from farm.capabilities.schemas import AiConversationView
from farm.db.pool import DbPool


class ConversationRecord(BaseModel):
    id: UUID
    ai: str
    account: str
    native_session_id: str | None
    turns: int
    tokens: int
    cost: Decimal
    last_job_id: UUID | None
    created_at: datetime
    updated_at: datetime


async def insert_conversation(conn: AsyncConnection[Any], *, ai: str, account: str) -> UUID:
    cur = await conn.execute(
        "insert into public.ai_conversations (ai, account) values (%s, %s) returning id", (ai, account)
    )
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("insert into ai_conversations returned no row")
    return UUID(str(row[0]))


async def point_to_job(conn: AsyncConnection[Any], conversation_id: UUID, job_id: UUID) -> None:
    await conn.execute(
        "update public.ai_conversations set last_job_id = %s where id = %s", (job_id, conversation_id)
    )


async def move_to_account(conn: AsyncConnection[Any], conversation_id: UUID, account: str) -> None:
    """Only for a conversation that has no native session yet (its first turn is being retried elsewhere)."""
    await conn.execute(
        "update public.ai_conversations set account = %s where id = %s and native_session_id is null",
        (account, conversation_id),
    )


async def apply_turn(
    conn: AsyncConnection[Any],
    conversation_id: UUID,
    *,
    job_id: UUID,
    account: str,
    native_session_id: str | None,
    tokens: int,
    cost_usd: Decimal,
    succeeded: bool,
) -> None:
    """Add a finished job to its conversation: totals always, the turn and the session only on success."""
    await conn.execute(
        """
        update public.ai_conversations set
          account = case when %(ok)s then %(account)s else account end,
          native_session_id = case when %(ok)s then coalesce(%(session)s, native_session_id)
                                   else native_session_id end,
          turns = turns + %(ok)s::int,
          tokens = tokens + %(tokens)s,
          cost = cost + %(cost)s,
          last_job_id = %(job)s
        where id = %(id)s
        """,
        {
            "id": conversation_id,
            "job": job_id,
            "account": account,
            "session": native_session_id,
            "tokens": tokens,
            "cost": cost_usd,
            "ok": succeeded,
        },
    )


class ConversationStore:
    def __init__(self, pool: DbPool) -> None:
        self._pool = pool

    async def get(self, conversation_id: UUID) -> ConversationRecord | None:
        async with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=class_row(ConversationRecord)) as cur,
        ):
            await cur.execute(
                "select id, ai, account, native_session_id, turns, tokens, cost, last_job_id, "
                "created_at, updated_at "
                "from public.ai_conversations where id = %s",
                (conversation_id,),
            )
            return await cur.fetchone()

    async def threads(
        self, *, ai: str | None = None, account: str | None = None, only_active: bool = False, limit: int = 50
    ) -> list[AiConversationView]:
        """The open threads, most recently used first, with the state of the last job and the running one."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                select c.id, c.ai, c.account, c.native_session_id, c.turns, c.tokens, c.cost, c.last_job_id,
                       lj.state, active.id, c.created_at, c.updated_at
                from public.ai_conversations c
                left join public.ai_jobs lj on lj.id = c.last_job_id
                left join lateral (
                    select j.id from public.ai_jobs j
                    where j.conversation_id = c.id and j.state in ('queued', 'running') limit 1
                ) active on true
                where (%(ai)s::text is null or c.ai = %(ai)s)
                  and (%(account)s::text is null or c.account = %(account)s)
                  and (not %(only_active)s or active.id is not null)
                order by c.updated_at desc, c.id
                limit %(limit)s
                """,
                {"ai": ai, "account": account, "only_active": only_active, "limit": limit},
            )
            rows = await cur.fetchall()
        return [
            AiConversationView(
                conversation_id=r[0],
                ai=r[1],
                account=r[2],
                native_session_id=r[3],
                turns=r[4],
                tokens=r[5],
                cost_usd=float(r[6]),
                last_job_id=r[7],
                last_job_state=r[8],
                active_job_id=r[9],
                created_at=r[10],
                updated_at=r[11],
            )
            for r in rows
        ]
