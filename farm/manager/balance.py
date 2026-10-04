"""Balance synchronization and reconciliation for provider accounts.

Supports:
- Syncing live provider balances via adapter.balance() -> balance_snapshots (source='api')
- Recording manual balance entries -> balance_snapshots (source='manual' or 'cli')
- Reconciling estimated ledger remaining vs provider actual balance:
  Drift > 10% raises an alert (kind='balance_drift', severity='warn').
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog

from farm.adapters import ADAPTERS, ApiAdapter
from farm.db.pool import DbPool
from farm.executors.base import ConnectionView
from farm.manager.alerts import create_alert

log = structlog.get_logger(__name__)

DRIFT_THRESHOLD = Decimal("0.10")  # 10% drift


async def compute_ledger_remaining(pool: DbPool, connection_id: str, unit: str) -> Decimal | None:
    """Compute the estimated remaining balance for a connection unit according to the quota ledger."""
    async with pool.connection() as conn:
        # Check if consumption_units defines a limit_value and period
        cur = await conn.execute(
            "select limit_value, period, reset_anchor from public.consumption_units "
            "where connection_id = %s and unit = %s",
            (connection_id, unit),
        )
        cu_row = await cur.fetchone()
        if cu_row is not None and cu_row[0] is not None:
            limit_val = Decimal(str(cu_row[0]))
            period = cu_row[1]
            anchor = cu_row[2]

            cur_p = await conn.execute(
                "select public.farm_period_start(%s, %s, now())",
                (period, anchor),
            )
            p_row = await cur_p.fetchone()
            period_start = p_row[0] if p_row else None

            # Get current period usage
            cur_u = await conn.execute(
                "select coalesce(sum(used), 0), coalesce(sum(reserved), 0) from public.quota_usage "
                "where connection_id = %s and unit = %s and period_start = %s",
                (connection_id, unit, period_start),
            )
            u_row = await cur_u.fetchone()
            used = Decimal(str(u_row[0])) if u_row else Decimal(0)
            return max(Decimal(0), limit_val - used)

        # Fallback: find latest previous snapshot and sum usage_events since that snapshot
        cur_snap = await conn.execute(
            "select remaining, at from public.balance_snapshots "
            "where connection_id = %s and unit = %s order by at desc limit 1",
            (connection_id, unit),
        )
        snap_row = await cur_snap.fetchone()
        if snap_row is not None:
            base_remaining = Decimal(str(snap_row[0]))
            snap_time = snap_row[1]
            cur_ev = await conn.execute(
                "select coalesce(sum(amount), 0) from public.usage_events "
                "where connection_id = %s and unit = %s and at >= %s",
                (connection_id, unit, snap_time),
            )
            ev_row = await cur_ev.fetchone()
            used_since = Decimal(str(ev_row[0])) if ev_row else Decimal(0)
            return max(Decimal(0), base_remaining - used_since)

    return None


async def record_balance_snapshot(
    pool: DbPool,
    connection_id: str,
    unit: str,
    remaining: Decimal | float | int,
    source: str = "manual",
    *,
    at: datetime | None = None,
    check_drift: bool = True,
) -> UUID:
    """Record a balance snapshot for a connection.

    Valid sources: 'api', 'manual', 'agent', 'cli'.
    If check_drift is True, compares remaining with estimated ledger remaining.
    If drift > 10%, raises an alert.
    """
    valid_sources = {"api", "manual", "agent", "cli"}
    if source not in valid_sources:
        raise ValueError(f"Invalid source '{source}'. Must be one of {valid_sources}")

    rem_dec = Decimal(str(remaining))

    if check_drift:
        ledger_rem = await compute_ledger_remaining(pool, connection_id, unit)
        if ledger_rem is not None:
            diff = abs(ledger_rem - rem_dec)
            denom = rem_dec if rem_dec > 0 else (ledger_rem if ledger_rem > 0 else Decimal(1))
            drift_pct = diff / denom
            if drift_pct > DRIFT_THRESHOLD:
                pct_str = f"{drift_pct * 100:.1f}%"
                msg = (
                    f"Balance drift {pct_str} on connection '{connection_id}' ({unit}): "
                    f"ledger estimated {ledger_rem} vs actual {rem_dec}"
                )
                ref = f"drift:{connection_id}:{unit}"
                await create_alert(pool, kind="balance_drift", severity="warn", message=msg, ref=ref)

    snapshot_id = uuid4()
    async with pool.connection() as conn:
        if at is not None:
            await conn.execute(
                "insert into public.balance_snapshots (id, connection_id, unit, remaining, source, at) "
                "values (%s, %s, %s, %s, %s, %s)",
                (snapshot_id, connection_id, unit, rem_dec, source, at),
            )
        else:
            await conn.execute(
                "insert into public.balance_snapshots (id, connection_id, unit, remaining, source) "
                "values (%s, %s, %s, %s, %s)",
                (snapshot_id, connection_id, unit, rem_dec, source),
            )

    log.info(
        "balance.snapshot_recorded",
        connection_id=connection_id,
        unit=unit,
        remaining=float(rem_dec),
        source=source,
    )
    return snapshot_id


async def sync_connection_balance(
    pool: DbPool,
    connection_id: str,
    *,
    adapter: ApiAdapter | None = None,
    check_drift: bool = True,
) -> list[dict[str, Any]]:
    """Fetch live balance using the connection's provider adapter and record snapshots."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select id, provider_id, auth_ref, meta, concurrency, rate_per_min "
            "from public.connections where id = %s",
            (connection_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"Unknown connection '{connection_id}'")

        conn_view = ConnectionView(
            id=row[0],
            provider_id=row[1],
            auth_ref=row[2],
            meta=row[3] or {},
            concurrency=row[4] or 1,
            rate_per_min=row[5],
        )

    ad = adapter
    owns_ad = False
    if ad is None:
        adapter_cls = ADAPTERS.get(conn_view.provider_id)
        if adapter_cls is None:
            log.info("balance.no_adapter", provider=conn_view.provider_id)
            return []
        ad = adapter_cls()
        owns_ad = True

    try:
        bal_res = await ad.balance(conn_view)
        if not bal_res.ok:
            log.warning("balance.fetch_failed", connection_id=connection_id, error=bal_res.error)
            return []

        recorded: list[dict[str, Any]] = []
        units_to_record = dict(bal_res.units)
        if not units_to_record and bal_res.remaining is not None:
            units_to_record[ad.balance_unit] = bal_res.remaining

        for unit, remaining in units_to_record.items():
            sid = await record_balance_snapshot(
                pool,
                connection_id=connection_id,
                unit=unit,
                remaining=remaining,
                source="api",
                check_drift=check_drift,
            )
            recorded.append({"unit": unit, "remaining": remaining, "snapshot_id": sid})

        return recorded
    finally:
        if owns_ad and ad is not None:
            await ad.aclose()


async def sync_all_balances(pool: DbPool, *, check_drift: bool = True) -> dict[str, list[dict[str, Any]]]:
    """Sync balances for all active connections whose providers have adapters."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select id, provider_id from public.connections where status = 'active'"
        )
        rows = await cur.fetchall()

    results: dict[str, list[dict[str, Any]]] = {}
    for conn_id, provider_id in rows:
        if provider_id in ADAPTERS:
            try:
                snapshots = await sync_connection_balance(pool, conn_id, check_drift=check_drift)
                if snapshots:
                    results[conn_id] = snapshots
            except Exception as exc:
                log.warning("balance.sync_all_error", connection_id=conn_id, error=str(exc))
    return results
