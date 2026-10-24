"""Connection health (M2): what the Farm remembers about each account between calls.

Persisted in ``connection_health`` (and ``connections.status``), so a restart keeps every circuit, cooldown
and exhausted flag. The router reads it when it builds the candidate list and reports every outcome here.

Effect of an outcome (``record_success`` / ``record_failure``):

=====================  ============  ===================================================================
outcome                counters      state
=====================  ============  ===================================================================
success                success +1    consecutive failures reset, circuit closed, cooldown cleared,
                                     ``last_error`` (+ kind, time) cleared, ``exhausted`` -> ``active``;
                                     a success that ends while a newer failure's cooldown runs changes
                                     none of these (the failure still explains the cooldown)
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

Circuit state transitions (``circuit``, ``cooldown_until``). A half-open circuit is a **probe lease**: the one
call that claimed it must end it, and a lease whose holder vanished runs out by itself. An EMPTY / BAD_REQUEST
outcome leaves a closed circuit alone, but it still ends a half-open probe (the circuit reopens).

========== ========================================== ================= =================================
from       event                                      to                cooldown_until
========== ========================================== ================= =================================
closed     CIRCUIT_KINDS failure, N-th in a row       open              now + open_seconds
closed     any other outcome                          closed            unchanged
open       cooldown not over                          open (not tried)  unchanged
open       ``claim_probe`` once the cooldown is over  half_open         now + lease (the probe runs)
half_open  ``claim_probe`` while the lease runs       half_open         unchanged (not tried)
half_open  ``claim_probe`` after the lease ran out    half_open         now + lease (a new probe)
half_open  success                                    closed            cleared
half_open  any failure: server, timeout, unknown,     open              now + open_seconds (doubled with
           rate limited, limit reached, auth, login,                    backoff), never shorter than the
           and also empty / bad_request                                 provider's own cooldown
half_open  ``release_probe`` (cancelled, shut down,   half_open         now (the next call probes)
           or the call was never made)
========== ========================================== ================= =================================
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Final

from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from farm.db.pool import DbPool, tenant_connection
from farm.executors.base import ErrorKind
from farm.resources.periods import next_period_start
from farm.secrets import redact

CIRCUIT_THRESHOLD: Final = 5
CIRCUIT_OPEN_S: Final = 120.0
CIRCUIT_MAX_OPEN_S: Final = 1800.0  # 30 minutes cap
DEFAULT_COOLDOWN_S: Final = 60.0
MAX_COOLDOWN_S: Final = 24 * 3600.0
EXHAUSTED_PROBE_S: Final = 3600.0
PROBE_LEASE_S: Final = 120.0
"""How long a half-open probe may run before another call may take it over (a probe whose process died)."""
RECENT_OUTCOMES: Final = 50
"""``connection_health.recent_outcomes`` keeps this many ``S`` / ``F`` marks (failover tie-break window)."""
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

    ``open`` and ``half_open`` both end at ``cooldown_until``; for ``half_open`` that is the probe lease, so a
    probe that never reported back blocks the account only until its lease has run out. A free ``open`` /
    ``half_open`` connection must still win ``claim_probe`` before it is called.
    """
    if circuit in ("open", "half_open"):
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


async def claim_probe(
    pool: DbPool, connection_id: str, now: datetime, *, lease_s: float = PROBE_LEASE_S
) -> bool:
    """Claim the single probe of a circuit that is open (cooldown over) or half-open (lease over).

    Row-locked claim in Postgres, so exactly one caller wins. The winner holds the probe for ``lease_s``; it
    must end it with ``record_success`` / ``record_failure`` or give it back with ``release_probe``.
    """
    async with tenant_connection(pool) as conn, conn.transaction():
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
        if circuit in ("open", "half_open") and (cooldown_until is None or cooldown_until <= now):
            await conn.execute(
                """
                update public.connection_health
                set circuit = 'half_open',
                    cooldown_until = %s
                where connection_id = %s
                """,
                (now + timedelta(seconds=lease_s), connection_id),
            )
            return True
        return False


async def release_probe(pool: DbPool, connection_id: str, now: datetime) -> bool:
    """Give back a probe that reached no verdict (cancelled, shut down, never made): the next call probes.

    Touches only a circuit that is still half-open, so it is safe after the probe already ended.
    """
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "update public.connection_health set cooldown_until = %s "
            "where connection_id = %s and circuit = 'half_open' returning connection_id",
            (now, connection_id),
        )
        return await cur.fetchone() is not None


async def reactivate_due(pool: DbPool, now: datetime) -> int:
    """Reactivate exhausted connections whose cooldown has passed using the SQL function."""
    async with tenant_connection(pool) as conn:
        cur = await conn.execute("select public.farm_reactivate_due(%s)", (now,))
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


async def record_success(pool: DbPool, connection_id: str, *, now: datetime) -> None:
    """Record a successful outcome: closes circuit, resets consecutive failures, clears cooldown + last error.

    ``last_error`` describes the failure the account is in, never a success (docs/QUALITY.md Q0): left in
    place, an old error reads as the account's state long after the account recovered.
    """
    async with tenant_connection(pool) as conn, conn.transaction():
        await conn.execute(
            "insert into public.connection_health (connection_id) values (%s) on conflict do nothing",
            (connection_id,),
        )
        await conn.execute(
            """
            update public.connection_health set
              success_count = success_count + 1,
              last_success_at = %(now)s,
              recent_outcomes = right(recent_outcomes || 'S', %(window)s),
              consecutive_failures = case when circuit <> 'half_open' and cooldown_until > %(now)s
                                          then consecutive_failures else 0 end,
              circuit = case when circuit <> 'half_open' and cooldown_until > %(now)s then circuit
                             else 'closed' end,
              open_seconds = case when circuit <> 'half_open' and cooldown_until > %(now)s
                                  then open_seconds else null end,
              last_error = case when circuit <> 'half_open' and cooldown_until > %(now)s
                                then last_error else null end,
              last_error_kind = case when circuit <> 'half_open' and cooldown_until > %(now)s
                                     then last_error_kind else null end,
              last_error_at = case when circuit <> 'half_open' and cooldown_until > %(now)s
                                   then last_error_at else null end,
              cooldown_until = case when circuit <> 'half_open' and cooldown_until > %(now)s
                                    then cooldown_until else null end
            where connection_id = %(id)s
            """,
            {"id": connection_id, "now": now, "window": RECENT_OUTCOMES},
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
    """Apply the failure rules of the module table to one connection, atomically.

    Every outcome ends a half-open probe: the circuit reopens, whatever the kind (``EMPTY`` / ``BAD_REQUEST``
    included, which otherwise change nothing).
    """
    neutral = kind in NEUTRAL_KINDS
    counts_toward_circuit = kind in CIRCUIT_KINDS
    error_text = redact(message or "")[:MAX_ERROR_CHARS] or None
    needs_login_alert = False

    async with tenant_connection(pool) as conn, conn.transaction():
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
        if neutral:
            pass
        elif kind is ErrorKind.RATE_LIMITED:
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
            needs_login_alert = True

        # Get current state from connection_health
        if not neutral:  # a neutral outcome leaves no trace, so it does not create the row either
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
        probe_ended = cur_circuit == "half_open"
        if neutral and not probe_ended:
            return

        new_circuit = cur_circuit
        new_open_s = cur_open_s
        new_consecutive = cur_consecutive + 1 if counts_toward_circuit else cur_consecutive
        circuit_cooldown: datetime | None = None

        if probe_ended:
            open_for = target_open_s
            if should_backoff:
                open_for = min(float(cur_open_s or target_open_s) * 2.0, CIRCUIT_MAX_OPEN_S)
            new_circuit = "open"
            new_open_s = int(open_for)
            circuit_cooldown = now + timedelta(seconds=open_for)
        elif counts_toward_circuit and new_consecutive >= target_threshold:
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

        # cooldown_until is the latest of any cooldowns, or unchanged when there is none. A probe that ended
        # replaces its lease with the fresh cooldown instead (the lease must not stretch the cooldown).
        await conn.execute(
            """
            update public.connection_health set
              failure_count = failure_count + %(counted)s,
              recent_outcomes = case when %(counted)s = 1
                                     then right(recent_outcomes || 'F', %(window)s) else recent_outcomes end,
              last_error_kind = case when %(counted)s = 1 then %(kind)s else last_error_kind end,
              last_error = case when %(counted)s = 1 then %(error)s else last_error end,
              last_error_at = case when %(counted)s = 1 then %(now)s else last_error_at end,
              consecutive_failures = %(consecutive)s,
              circuit = %(circuit)s,
              open_seconds = %(open_seconds)s,
              cooldown_until = case when %(probe_ended)s then %(cooldown)s else greatest(
                  coalesce(cooldown_until, %(circuit_cooldown)s),
                  coalesce(%(cooldown)s, %(circuit_cooldown)s)
              ) end
            where connection_id = %(id)s
            """,
            {
                "id": connection_id,
                "counted": 0 if neutral else 1,
                "window": RECENT_OUTCOMES,
                "kind": kind.value,
                "error": error_text,
                "now": now,
                "consecutive": new_consecutive,
                "circuit": new_circuit,
                "open_seconds": new_open_s,
                "probe_ended": probe_ended,
                "cooldown": final_cooldown,
                "circuit_cooldown": circuit_cooldown,
            },
        )

    if needs_login_alert:
        # After the transaction: the alert takes its own pooled connection, and holding two at once would
        # starve a small pool when many accounts fail to authenticate together.
        from farm.manager.alerts import create_alert

        await create_alert(
            pool,
            kind="needs_login",
            severity="critical",
            message=f"Account {connection_id} authentication failed. Run: farm ai login {connection_id}",
            ref=f"login:{connection_id}",
            notify_telegram=False,
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
