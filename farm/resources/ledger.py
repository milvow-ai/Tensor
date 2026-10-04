"""Quota ledger: thin async wrappers over the atomic SQL functions (migration 0001).

The SQL functions ``farm_reserve`` / ``farm_commit`` / ``farm_release`` / ``farm_expire_reservations`` are the
only way quota changes; nothing here does arithmetic. All arguments are passed as typed parameters (explicit
casts, so Python floats work too), and amounts travel as ``Decimal`` to avoid binary float noise.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from farm.db.pool import DbPool

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


async def commit(pool: DbPool, res_id: UUID | str, actual: Amount | None = None) -> None:
    """Settle a reservation: its amount leaves ``reserved``, ``actual`` (default: the reserved amount) is
    added to ``used``. Pass ``0`` when the call failed and the unit is charged on success. Idempotent."""
    async with pool.connection() as conn:
        await conn.execute(
            "select public.farm_commit(%s::uuid, %s::numeric)",
            (res_id, None if actual is None else _num(actual)),
        )


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
