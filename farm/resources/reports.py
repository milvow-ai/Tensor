"""Read-only reports over the Farm's state: capacity, usage and the resource inventory.

One implementation behind the agent tools (``get_capacity``, ``get_usage``, ``list_resources``) and the
``farm status`` command, so a person at the terminal and an agent see the same numbers. Everything is
aggregated in SQL (never in the client) and never contains a secret: connections show their id, label and
state, not their ``auth_ref``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from farm.db.pool import DbPool
from farm.resources.health import block_reason
from farm.resources.periods import next_period_start


class UnitCapacity(BaseModel):
    unit: str
    limit: float | None
    used: float
    reserved: float
    remaining: float | None
    """``limit - used - reserved``; ``None`` when the unit has no limit."""
    period: str
    next_reset_at: datetime | None


class ConnectionCapacity(BaseModel):
    id: str
    label: str
    status: str
    circuit: str
    cooldown_until: datetime | None
    available: bool
    unavailable_reason: str | None
    last_error_kind: str | None
    units: list[UnitCapacity]


class PoolCapacity(BaseModel):
    provider_id: str
    name: str
    kind: str
    enabled: bool
    connections: list[ConnectionCapacity]


async def capacity_report(pool: DbPool, now: datetime, capability: str | None = None) -> list[PoolCapacity]:
    """Per pool and connection: state, circuit, cooldown, what is left of every unit and when it resets.

    With ``capability``: only the pools on its route, in route order (otherwise all pools by id).
    """
    async with pool.connection() as conn:
        if capability is None:
            cur = await conn.execute(
                "select id, name, kind, enabled from public.providers order by id",
            )
        else:
            cur = await conn.execute(
                "select p.id, p.name, p.kind, p.enabled from public.capability_routes r "
                "join public.providers p on p.id = r.provider_id "
                "where r.capability = %s and r.enabled order by r.position, p.id",
                (capability,),
            )
        providers = await cur.fetchall()
        ids = [p[0] for p in providers]
        cur = await conn.execute(
            "select cn.id, cn.provider_id, coalesce(cn.label, cn.id), cn.status, "
            "coalesce(h.circuit, 'closed'), h.cooldown_until, h.last_error_kind from public.connections cn "
            "left join public.connection_health h on h.connection_id = cn.id "
            "where cn.provider_id = any(%s) order by cn.priority, cn.id",
            (ids,),
        )
        connections = await cur.fetchall()
        cur = await conn.execute(
            "select cu.connection_id, cu.unit, cu.limit_value, cu.period, cu.reset_anchor, "
            "coalesce(qu.used, 0), coalesce(qu.reserved, 0) from public.consumption_units cu "
            "join public.connections cn on cn.id = cu.connection_id "
            "left join public.quota_usage qu on qu.connection_id = cu.connection_id and qu.unit = cu.unit "
            "and qu.period_start = public.farm_period_start(cu.period, cu.reset_anchor, %s) "
            "where cn.provider_id = any(%s) order by cu.connection_id, cu.unit",
            (now, ids),
        )
        unit_rows = await cur.fetchall()

    units: dict[str, list[UnitCapacity]] = {}
    for connection_id, unit, limit, period, anchor, used, reserved in unit_rows:
        units.setdefault(connection_id, []).append(
            UnitCapacity(
                unit=unit,
                limit=None if limit is None else float(limit),
                used=float(used),
                reserved=float(reserved),
                remaining=None if limit is None else float(limit - used - reserved),
                period=period,
                next_reset_at=next_period_start(period, anchor, now),
            )
        )
    by_provider: dict[str, list[ConnectionCapacity]] = {}
    for cid, provider_id, label, status, circuit, cooldown, last_error in connections:
        reason = block_reason(status, circuit, cooldown, now)
        by_provider.setdefault(provider_id, []).append(
            ConnectionCapacity(
                id=cid,
                label=label,
                status=status,
                circuit=circuit,
                cooldown_until=cooldown,
                available=reason is None,
                unavailable_reason=reason,
                last_error_kind=last_error,
                units=units.get(cid, []),
            )
        )
    return [
        PoolCapacity(
            provider_id=pid, name=name, kind=kind, enabled=enabled, connections=by_provider.get(pid, [])
        )
        for pid, name, kind, enabled in providers
    ]


async def list_resources(pool: DbPool) -> dict[str, Any]:
    """The inventory: capabilities with their routes, providers with their connections and units."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select c.name, c.kind, c.description, c.default_strategy, c.cache_ttl_seconds, "
            "coalesce(array_agg(r.provider_id order by r.position) "
            "filter (where r.provider_id is not null), '{}') "
            "from public.capabilities c left join public.capability_routes r "
            "on r.capability = c.name and r.enabled group by c.name order by c.name"
        )
        capabilities = await cur.fetchall()
        cur = await conn.execute("select id, name, kind, executor, enabled from public.providers order by id")
        providers = await cur.fetchall()
        cur = await conn.execute(
            "select id, provider_id, coalesce(label, id), status, priority, scope from public.connections "
            "order by provider_id, priority, id"
        )
        connections = await cur.fetchall()
        cur = await conn.execute(
            "select connection_id, unit, limit_value, period, charged_on from public.consumption_units "
            "order by connection_id, unit"
        )
        units = await cur.fetchall()

    units_by_connection: dict[str, list[dict[str, Any]]] = {}
    for connection_id, unit, limit, period, charged_on in units:
        units_by_connection.setdefault(connection_id, []).append(
            {
                "unit": unit,
                "limit": None if limit is None else float(limit),
                "period": period,
                "charged_on": charged_on,
            }
        )
    connections_by_provider: dict[str, list[dict[str, Any]]] = {}
    for cid, provider_id, label, status, priority, scope in connections:
        connections_by_provider.setdefault(provider_id, []).append(
            {
                "id": cid,
                "label": label,
                "status": status,
                "priority": priority,
                "scope": scope,
                "units": units_by_connection.get(cid, []),
            }
        )
    return {
        "capabilities": [
            {
                "name": name,
                "kind": kind,
                "description": description,
                "strategy": strategy,
                "cache_ttl_seconds": ttl,
                "routes": routes,
            }
            for name, kind, description, strategy, ttl, routes in capabilities
        ],
        "providers": [
            {
                "id": pid,
                "name": name,
                "kind": kind,
                "executor": executor,
                "enabled": enabled,
                "connections": connections_by_provider.get(pid, []),
            }
            for pid, name, kind, executor, enabled in providers
        ],
    }


async def usage_report(pool: DbPool, now: datetime, days: int = 30) -> dict[str, Any]:
    """What the last ``days`` days consumed and cost per connection/unit, and runs per capability/status."""
    since = now - timedelta(days=days)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select connection_id, unit, sum(amount), sum(cost_usd), count(*) from public.usage_events "
            "where at >= %s and kind = 'actual' group by connection_id, unit order by connection_id, unit",
            (since,),
        )
        usage = await cur.fetchall()
        cur = await conn.execute(
            "select capability, status, count(*), coalesce(sum(cost_usd), 0), count(*) filter (where cached) "
            "from public.runs where started_at >= %s group by capability, status order by capability, status",
            (since,),
        )
        runs = await cur.fetchall()
    return {
        "days": days,
        "since": since.isoformat(),
        "total_cost_usd": float(sum(row[3] for row in runs)),
        "by_connection": [
            {"connection_id": c, "unit": u, "amount": float(a), "cost_usd": float(cost), "events": n}
            for c, u, a, cost, n in usage
        ],
        "runs": [
            {"capability": cap, "status": status, "count": n, "cost_usd": float(cost), "cached_runs": cached}
            for cap, status, n, cost, cached in runs
        ],
    }
