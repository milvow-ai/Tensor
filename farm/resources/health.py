"""Connection health (M1 subset): what the Farm remembers about each account between calls.

Persisted in ``connection_health`` (and ``connections.status``), so a restart keeps every circuit, cooldown
and exhausted flag. The router reads it when it builds the candidate list and reports every outcome here.

Effect of an outcome (``record_success`` / ``record_failure``):

=====================  ============  ===================================================================
outcome                counters      state
=====================  ============  ===================================================================
success                success +1    consecutive failures reset, circuit closed, cooldown cleared,
                                     ``exhausted`` -> ``active``
RATE_LIMITED           failure +1    cooldown until now + retry_after (default 60 s, at most 24 h)
LIMIT_REACHED          failure +1    status ``exhausted``; cooldown until the provider's ``reset_at``, else
                                     the next period start of the connection's exhausted units, else 1 h
                                     (a probe after that time finds out whether credits came back)
AUTH / NEEDS_LOGIN     failure +1    status ``needs_login`` (only a person clears it)
SERVER/TIMEOUT/UNKNOWN failure +1,   after 5 consecutive failures the circuit opens for 120 s
                       consecutive +1
EMPTY / BAD_REQUEST    none          none: the provider answered correctly, the request or the data was the
                                     problem, so the account's health is neither helped nor hurt
=====================  ============  ===================================================================

A success that arrives while a cooldown or open circuit is still running (a call that started before the
trouble) only counts; it does not lift the cooldown. M2 extends this module (half-open probes with a single
claim, backoff, jitter, per-connection thresholds).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from farm.db.pool import DbPool
from farm.executors.base import ErrorKind
from farm.resources.periods import next_period_start
from farm.secrets import redact

CIRCUIT_THRESHOLD: Final = 5
CIRCUIT_OPEN_S: Final = 120.0
DEFAULT_COOLDOWN_S: Final = 60.0
MAX_COOLDOWN_S: Final = 24 * 3600.0
EXHAUSTED_PROBE_S: Final = 3600.0
MAX_ERROR_CHARS: Final = 500

CIRCUIT_KINDS: Final = frozenset({ErrorKind.SERVER, ErrorKind.TIMEOUT, ErrorKind.UNKNOWN})
NEUTRAL_KINDS: Final = frozenset({ErrorKind.EMPTY, ErrorKind.BAD_REQUEST})
LOGIN_KINDS: Final = frozenset({ErrorKind.AUTH, ErrorKind.NEEDS_LOGIN})


def unavailable_reason(circuit: str, cooldown_until: datetime | None, now: datetime) -> str | None:
    """Why a connection may not be tried right now: ``circuit_open``, ``cooldown`` or ``None`` (free).

    An open circuit whose window has passed counts as free: the next call is the probe (a success closes
    it, a failure re-opens it because the consecutive-failure count is still at the threshold).
    """
    if cooldown_until is None or cooldown_until <= now:
        return None
    return "circuit_open" if circuit == "open" else "cooldown"


def block_reason(status: str, circuit: str, cooldown_until: datetime | None, now: datetime) -> str | None:
    """Why a connection may not be tried right now, or ``None`` when it may.

    ``disabled`` / ``paused`` / ``needs_login`` are operator or login states. ``exhausted`` blocks until its
    cooldown (the provider's reset time) has passed; after that the connection is tried again as a probe and
    a success flips it back to ``active``. An ``exhausted`` connection without a cooldown stays blocked.
    Otherwise an open circuit (``circuit_open``) or a running cooldown (``cooldown``) blocks.
    """
    if status in ("disabled", "paused", "needs_login"):
        return status
    if status == "exhausted" and (cooldown_until is None or cooldown_until > now):
        return "exhausted"
    return unavailable_reason(circuit, cooldown_until, now)


async def record_success(pool: DbPool, connection_id: str, *, now: datetime) -> None:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "insert into public.connection_health (connection_id) values (%s) on conflict do nothing",
            (connection_id,),
        )
        await conn.execute(
            """
            update public.connection_health set
              success_count = success_count + 1,
              last_success_at = %(now)s,
              consecutive_failures = case when cooldown_until > %(now)s then consecutive_failures else 0 end,
              circuit = case when cooldown_until > %(now)s then circuit else 'closed' end,
              cooldown_until = case when cooldown_until > %(now)s then cooldown_until else null end
            where connection_id = %(id)s
            """,
            {"id": connection_id, "now": now},
        )
        await conn.execute(
            "update public.connections set status = 'active' where id = %s and status = 'exhausted'",
            (connection_id,),
        )


async def record_failure(
    pool: DbPool,
    connection_id: str,
    kind: ErrorKind,
    message: str | None,
    *,
    now: datetime,
    retry_after_s: float | None = None,
    reset_at: datetime | None = None,
) -> None:
    """Apply the failure rules of the module table to one connection, atomically."""
    if kind in NEUTRAL_KINDS:
        return
    counts_toward_circuit = kind in CIRCUIT_KINDS
    error_text = redact(message or "")[:MAX_ERROR_CHARS] or None

    async with pool.connection() as conn, conn.transaction():
        cooldown: datetime | None = None
        if kind is ErrorKind.RATE_LIMITED:
            seconds = DEFAULT_COOLDOWN_S if retry_after_s is None else max(0.0, retry_after_s)
            cooldown = now + timedelta(seconds=min(seconds, MAX_COOLDOWN_S))
        elif kind is ErrorKind.LIMIT_REACHED:
            cooldown = await _exhausted_until(conn, connection_id, reset_at, now)
            await conn.execute(
                "update public.connections set status = 'exhausted' where id = %s and status = 'active'",
                (connection_id,),
            )
        elif kind in LOGIN_KINDS:
            await conn.execute(
                "update public.connections set status = 'needs_login' "
                "where id = %s and status in ('active', 'exhausted')",
                (connection_id,),
            )

        await conn.execute(
            "insert into public.connection_health (connection_id) values (%s) on conflict do nothing",
            (connection_id,),
        )
        await conn.execute(
            """
            update public.connection_health set
              failure_count = failure_count + 1,
              last_error_kind = %(kind)s,
              last_error = %(error)s,
              last_error_at = %(now)s,
              consecutive_failures = consecutive_failures + %(counts)s::int,
              circuit = case when %(counts)s and consecutive_failures + 1 >= %(threshold)s
                             then 'open' else circuit end,
              cooldown_until = nullif(greatest(
                  coalesce(cooldown_until, '-infinity'::timestamptz),
                  coalesce(%(cooldown)s::timestamptz, '-infinity'::timestamptz),
                  case when %(counts)s and consecutive_failures + 1 >= %(threshold)s
                       then %(open_until)s::timestamptz else '-infinity'::timestamptz end
              ), '-infinity'::timestamptz)
            where connection_id = %(id)s
            """,
            {
                "id": connection_id,
                "kind": kind.value,
                "error": error_text,
                "now": now,
                "counts": counts_toward_circuit,
                "threshold": CIRCUIT_THRESHOLD,
                "cooldown": cooldown,
                "open_until": now + timedelta(seconds=CIRCUIT_OPEN_S),
            },
        )


async def _exhausted_until(
    conn: AsyncConnection[TupleRow], connection_id: str, reset_at: datetime | None, now: datetime
) -> datetime:
    """When to probe an exhausted connection again (see the module table)."""
    if reset_at is not None and reset_at > now:
        return reset_at
    cur = await conn.execute(
        """
        select cu.period, cu.reset_anchor,
               coalesce(qu.used + qu.reserved >= cu.limit_value, false) as at_limit
          from public.consumption_units cu
          left join public.quota_usage qu
            on qu.connection_id = cu.connection_id and qu.unit = cu.unit
           and qu.period_start = public.farm_period_start(cu.period, cu.reset_anchor, %s)
         where cu.connection_id = %s
        """,
        (now, connection_id),
    )
    rows = await cur.fetchall()
    boundaries = [(at_limit, next_period_start(period, anchor, now)) for period, anchor, at_limit in rows]
    exhausted = [b for at_limit, b in boundaries if at_limit and b is not None]
    anything = [b for _, b in boundaries if b is not None]
    candidates = exhausted or anything
    return min(candidates) if candidates else now + timedelta(seconds=EXHAUSTED_PROBE_S)
