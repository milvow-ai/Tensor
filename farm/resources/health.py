"""Connection health (M2): what the Farm remembers about each account between calls.

Persisted in ``connection_health`` (and ``connections.status``), so a restart keeps every circuit, cooldown
and exhausted flag. The router reads it when it builds the candidate list and reports every outcome here.

Effect of an outcome (``record_success`` / ``record_failure``):

=====================  ============  ===================================================================
outcome                counters      state
=====================  ============  ===================================================================
success                success +1    consecutive failures reset, circuit closed, cooldown cleared,
                                     ``exhausted`` -> ``active``
RATE_LIMITED           failure +1    cooldown until now + retry_after (default 60 s, jitter +-20%, cap 24 h)
LIMIT_REACHED          failure +1    status ``exhausted``; cooldown until the provider's ``reset_at``, else
                                     the next period start of the connection's exhausted units, else 1 h
                                     (auto-active on read + farm_reactivate_due)
AUTH / NEEDS_LOGIN     failure +1    status ``needs_login`` + alert (no circuit; only a person clears it)
SERVER/TIMEOUT/UNKNOWN failure +1,   after N consecutive failures (default 5, override in meta.circuit)
                       consecutive +1 the circuit opens for open_seconds (default 120 s) then half-open:
                                     exactly one probe request allowed (row-locked claim),
                                     success -> closed, failure -> open with doubled open time (cap 30 min)
EMPTY / BAD_REQUEST    none          none: the provider answered correctly, the request or the data was the
                                     problem, so the account's health is neither helped nor hurt
=====================  ============  ===================================================================
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from farm.db.pool import DbPool
from farm.executors.base import ErrorKind
from farm.resources.periods import next_period_start
from farm.secrets import redact

CIRCUIT_THRESHOLD: Final = 5
CIRCUIT_OPEN_S: Final = 120.0
CIRCUIT_MAX_OPEN_S: Final = 1800.0  # 30 minutes cap
DEFAULT_COOLDOWN_S: Final = 60.0
MAX_COOLDOWN_S: Final = 24 * 3600.0
EXHAUSTED_PROBE_S: Final = 3600.0
MAX_ERROR_CHARS: Final = 500

CIRCUIT_KINDS: Final = frozenset({ErrorKind.SERVER, ErrorKind.TIMEOUT, ErrorKind.UNKNOWN})
NEUTRAL_KINDS: Final = frozenset({ErrorKind.EMPTY, ErrorKind.BAD_REQUEST})
LOGIN_KINDS: Final = frozenset({ErrorKind.AUTH, ErrorKind.NEEDS_LOGIN})


def get_circuit_config(meta: Mapping[str, Any] | None) -> tuple[int, float, bool]:
    """Extract (threshold, open_seconds, backoff) from connection meta.circuit or defaults."""
    if not meta:
        return (CIRCUIT_THRESHOLD, CIRCUIT_OPEN_S, False)
    c = meta.get("circuit")
    if isinstance(c, int) and c > 0:
        return (c, CIRCUIT_OPEN_S, False)
    if isinstance(c, dict):
        threshold = (
            c.get("failures")
            or c.get("threshold")
            or c.get("consecutive_failures")
            or CIRCUIT_THRESHOLD
        )
        open_s = c.get("open_seconds") or c.get("open_s") or CIRCUIT_OPEN_S
        backoff = bool(c.get("backoff", False))
        return (int(threshold), float(open_s), backoff)
    return (CIRCUIT_THRESHOLD, CIRCUIT_OPEN_S, False)


def unavailable_reason(circuit: str, cooldown_until: datetime | None, now: datetime) -> str | None:
    """Why a connection may not be tried right now: ``circuit_open``, ``cooldown`` or ``None`` (free).

    When circuit is 'half_open', a probe is already in flight, so others see 'circuit_open'.
    When circuit is 'open' and cooldown has passed, it returns None to allow the half-open probe claim.
    """
    if circuit == "half_open":
        return "circuit_open"
    if circuit == "open":
        if cooldown_until is not None and cooldown_until > now:
            return "circuit_open"
        return None
    if cooldown_until is not None and cooldown_until > now:
        return "cooldown"
    return None


def block_reason(status: str, circuit: str, cooldown_until: datetime | None, now: datetime) -> str | None:
    """Why a connection may not be tried right now, or ``None`` when it may.

    ``disabled`` / ``paused`` / ``needs_login`` are operator or login states.
    ``exhausted`` blocks until its cooldown has passed; after that it auto-activates.
    """
    if status in ("disabled", "paused", "needs_login"):
        return status
    if status == "exhausted" and (cooldown_until is None or cooldown_until > now):
        return "exhausted"
    return unavailable_reason(circuit, cooldown_until, now)


async def claim_probe(pool: DbPool, connection_id: str, now: datetime) -> bool:
    """Attempt to claim the single half-open probe for an open circuit whose window has passed.

    Row-locked claim in Postgres. Exactly one probe request is allowed.
    Returns True if claimed (circuit transitioned to half_open), False otherwise.
    """
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            select circuit, cooldown_until
            from public.connection_health
            where connection_id = %s
            for update
            """,
            (connection_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        circuit, cooldown_until = row[0], row[1]
        if circuit == "open" and (cooldown_until is None or cooldown_until <= now):
            await conn.execute(
                """
                update public.connection_health
                set circuit = 'half_open',
                    cooldown_until = null
                where connection_id = %s
                """,
                (connection_id,),
            )
            return True
        return False


async def reactivate_due(pool: DbPool, now: datetime) -> int:
    """Reactivate exhausted connections whose cooldown has passed using the SQL function."""
    async with pool.connection() as conn:
        cur = await conn.execute("select public.farm_reactivate_due(%s)", (now,))
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


async def record_success(pool: DbPool, connection_id: str, *, now: datetime) -> None:
    """Record a successful outcome: closes circuit, resets consecutive failures, clears cooldown."""
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
              open_seconds = case when cooldown_until > %(now)s then open_seconds else null end,
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
    jitter: bool = False,
    threshold: int | None = None,
    open_seconds: float | None = None,
    backoff: bool | None = None,
) -> None:
    """Apply the failure rules of the module table to one connection, atomically."""
    if kind in NEUTRAL_KINDS:
        return
    counts_toward_circuit = kind in CIRCUIT_KINDS
    error_text = redact(message or "")[:MAX_ERROR_CHARS] or None

    async with pool.connection() as conn, conn.transaction():
        cur_meta = await conn.execute(
            "select meta from public.connections where id = %s", (connection_id,)
        )
        meta_row = await cur_meta.fetchone()
        meta = meta_row[0] if meta_row else {}
        cfg_threshold, cfg_open_s, cfg_backoff = get_circuit_config(meta)
        target_threshold = threshold if threshold is not None else cfg_threshold
        target_open_s = open_seconds if open_seconds is not None else cfg_open_s
        should_backoff = backoff if backoff is not None else cfg_backoff

        cooldown: datetime | None = None
        if kind is ErrorKind.RATE_LIMITED:
            if retry_after_s is not None:
                base_s = max(0.0, retry_after_s)
                if jitter and bool(meta.get("jitter", False)):
                    base_s = base_s * random.uniform(0.8, 1.2)
            else:
                base_s = DEFAULT_COOLDOWN_S
                if jitter:
                    base_s = base_s * random.uniform(0.8, 1.2)
            cooldown = now + timedelta(seconds=min(base_s, MAX_COOLDOWN_S))
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
            # Create alert for needs_login
            from farm.manager.alerts import create_alert

            alert_msg = f"Account {connection_id} authentication failed. Run: farm ai login {connection_id}"
            await create_alert(
                pool,
                kind="needs_login",
                severity="critical",
                message=alert_msg,
                ref=f"login:{connection_id}",
                notify_telegram=False,
            )

        # Get current state from connection_health
        await conn.execute(
            "insert into public.connection_health (connection_id) values (%s) on conflict do nothing",
            (connection_id,),
        )
        cur_health = await conn.execute(
            "select circuit, consecutive_failures, open_seconds from public.connection_health "
            "where connection_id = %s for update",
            (connection_id,),
        )
        health_row = await cur_health.fetchone()
        cur_circuit = health_row[0] if health_row else "closed"
        cur_consecutive = (health_row[1] if health_row else 0) or 0
        cur_open_s = health_row[2] if health_row else None

        new_circuit = cur_circuit
        new_open_s = cur_open_s
        new_consecutive = cur_consecutive
        circuit_cooldown: datetime | None = None

        if counts_toward_circuit:
            new_consecutive = cur_consecutive + 1
            if cur_circuit == "half_open":
                if should_backoff:
                    prev_open = float(cur_open_s or target_open_s)
                    doubled = min(prev_open * 2.0, CIRCUIT_MAX_OPEN_S)
                    new_circuit = "open"
                    new_open_s = int(doubled)
                    circuit_cooldown = now + timedelta(seconds=doubled)
                else:
                    new_circuit = "open"
                    new_open_s = int(target_open_s)
                    circuit_cooldown = now + timedelta(seconds=target_open_s)
            elif new_consecutive >= target_threshold:
                new_circuit = "open"
                new_open_s = int(target_open_s)
                circuit_cooldown = now + timedelta(seconds=target_open_s)

        final_cooldown: datetime | None = None
        if cooldown is not None and circuit_cooldown is not None:
            final_cooldown = max(cooldown, circuit_cooldown)
        elif cooldown is not None:
            final_cooldown = cooldown
        elif circuit_cooldown is not None:
            final_cooldown = circuit_cooldown

        # Set cooldown_until to the latest of any cooldowns, or NULL if none
        # (None means no cooldown, not "-infinity", so we pass None explicitly and handle it in SQL)
        await conn.execute(
            """
            update public.connection_health set
              failure_count = failure_count + 1,
              last_error_kind = %(kind)s,
              last_error = %(error)s,
              last_error_at = %(now)s,
              consecutive_failures = %(consecutive)s,
              circuit = %(circuit)s,
              open_seconds = %(open_seconds)s,
              cooldown_until = greatest(
                  coalesce(cooldown_until, %(circuit_cooldown)s),
                  coalesce(%(cooldown)s, %(circuit_cooldown)s)
              )
            where connection_id = %(id)s
            """,
            {
                "id": connection_id,
                "kind": kind.value,
                "error": error_text,
                "now": now,
                "consecutive": new_consecutive,
                "circuit": new_circuit,
                "open_seconds": new_open_s,
                "cooldown": final_cooldown,
                "circuit_cooldown": circuit_cooldown,
            },
        )


async def _exhausted_until(
    conn: AsyncConnection[TupleRow], connection_id: str, reset_at: datetime | None, now: datetime
) -> datetime:
    """When to probe an exhausted connection again."""
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
