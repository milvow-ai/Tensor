"""Quota ledger: thin async wrappers over the atomic SQL functions (migration 0001).

The SQL functions ``farm_reserve`` / ``farm_commit`` / ``farm_release`` / ``farm_expire_reservations`` are the
only way quota changes; nothing here does arithmetic. All arguments are passed as typed parameters (explicit
casts, so Python floats work too), and amounts travel as ``Decimal`` to avoid binary float noise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import structlog
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

from farm.db.pool import DbPool
from farm.resources.periods import next_period_start

ROLLING_WINDOW = timedelta(hours=5)

log = structlog.get_logger(__name__)

type Amount = Decimal | float | int


def _num(value: Amount) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


async def reserve(
    pool: DbPool,
    conn_id: str,
    unit: str,
    amount: Amount,
    request_id: UUID | str | None = None,
    ttl_s: int = 300,
) -> UUID | None:
    """Reserve ``amount`` of ``unit`` on a connection.

    Returns the reservation id, or ``None`` when ``used + reserved + amount`` would exceed the limit.
    Raises (``psycopg.errors.NoData``) when the connection has no such unit.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select public.farm_reserve(%s::text, %s::text, %s::numeric, %s::uuid, %s::int)",
            (conn_id, unit, _num(amount), request_id, ttl_s),
        )
        row = await cur.fetchone()
    if row is None or row[0] is None:
        return None
    value = row[0]
    return value if isinstance(value, UUID) else UUID(str(value))


async def commit(pool: DbPool, res_id: UUID | str, actual: Amount | None = None) -> bool:
    """Settle a reservation: its amount leaves ``reserved``, ``actual`` (default: the reserved amount) is
    added to ``used``. Pass ``0`` when the call failed and the unit is charged on success. Idempotent.

    The real usage is always recorded, even when it is more than the estimate. If it carries the unit past its
    limit the window is marked exhausted (see ``_block_overrun``): the account takes no call until it frees.

    A reservation that expired before its call finished is still charged (a *late commit*: the call really
    ran), and a warning says so; returns ``True`` in that case.
    """
    async with pool.connection() as conn:
        await conn.execute(
            "select public.farm_commit(%s::uuid, %s::numeric)",
            (res_id, None if actual is None else _num(actual)),
        )
        cur = await conn.execute(
            "select connection_id, unit, actual, late from public.quota_reservations where id = %s",
            (res_id,),
        )
        settled = await cur.fetchone()
        if settled is not None and settled[2] is not None:
            await _block_overrun(conn, str(settled[0]), str(settled[1]))
    if settled is None or not settled[3]:
        return False
    log.warning(
        "ledger.late_commit",
        reservation_id=str(res_id),
        connection_id=settled[0],
        unit=settled[1],
        actual=str(settled[2]),
        reason="the reservation had expired before the call finished; the usage was recorded anyway",
    )
    return True


async def _block_overrun(conn: AsyncConnection[TupleRow], connection_id: str, unit: str) -> None:
    """Mark the account exhausted when ``used + reserved`` of ``unit`` is past its limit (audit R3b).

    An executor can report more usage than the estimate that was reserved. The usage stays recorded; the
    account is set ``exhausted`` with a cooldown until the window frees (the next period start, or for
    ``rolling_5h`` the moment enough old usage has left the sliding window), so ``farm_reactivate_due`` brings
    it back and no call is made before. Idempotent: an account that is already blocked keeps the later end.
    """
    async with conn.transaction():
        cur = await conn.execute(
            "select period, reset_anchor, limit_value, "
            "public.farm_quota_consumed(connection_id, unit, now()) "
            "from public.consumption_units where connection_id = %s and unit = %s for update",
            (connection_id, unit),
        )
        row = await cur.fetchone()
        if row is None or row[2] is None or row[3] <= row[2]:
            return
        period, anchor, limit, consumed = row
        now = datetime.now(UTC)
        until: datetime | None
        if period == "rolling_5h":
            until = await _rolling_free_at(conn, connection_id, unit, Decimal(consumed) - Decimal(limit), now)
        else:
            until = next_period_start(period, anchor, now)
        if (
            until is None
        ):  # a 'total' / 'none' unit never frees: stay blocked until the owner raises the limit
            until = now + timedelta(days=3650)
        await conn.execute(
            "update public.connections set status = 'exhausted' where id = %s and status = 'active'",
            (connection_id,),
        )
        await conn.execute(
            "insert into public.connection_health (connection_id, cooldown_until) values (%s, %s) "
            "on conflict (connection_id) do update set "
            "cooldown_until = greatest("
            "coalesce(public.connection_health.cooldown_until, excluded.cooldown_until), "
            "excluded.cooldown_until)",
            (connection_id, until),
        )
        await conn.execute(
            "update public.consumption_units set next_reset_at = %s where connection_id = %s and unit = %s",
            (until, connection_id, unit),
        )
    log.warning(
        "ledger.overrun",
        connection_id=connection_id,
        unit=unit,
        consumed=str(consumed),
        limit=str(limit),
        blocked_until=until.isoformat(),
        reason="the call used more than its estimate and the window is full; blocked until it frees",
    )


async def _rolling_free_at(
    conn: AsyncConnection[TupleRow], connection_id: str, unit: str, excess: Decimal, now: datetime
) -> datetime:
    """When the sliding 5-hour sum has dropped by ``excess``: enough of the oldest usage has left it."""
    cur = await conn.execute(
        "select status, coalesce(actual, amount), updated_at from public.quota_reservations "
        "where connection_id = %s and unit = %s "
        "and (status = 'reserved' or (status = 'committed' and updated_at > %s)) "
        "order by updated_at, id",
        (connection_id, unit, now - ROLLING_WINDOW),
    )
    freed = Decimal(0)
    for status, amount, at in await cur.fetchall():
        if status != "committed":
            continue  # a held reservation does not leave the window by time
        freed += Decimal(amount)
        if freed >= excess:
            return at + ROLLING_WINDOW  # type: ignore[no-any-return]  # psycopg rows are untyped
    return now + ROLLING_WINDOW


async def release(pool: DbPool, res_id: UUID | str) -> None:
    """Give a reservation back without charging. Idempotent."""
    async with pool.connection() as conn:
        await conn.execute("select public.farm_release(%s::uuid)", (res_id,))


async def expire(pool: DbPool) -> int:
    """Release every reservation past its ``expires_at``; returns how many were expired."""
    async with pool.connection() as conn:
        cur = await conn.execute("select public.farm_expire_reservations()")
        row = await cur.fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0
