"""Registry <-> database: ``config/registry.yaml`` seeds Postgres, Postgres is the runtime source of truth.

``sync_registry`` applies a validated :class:`Registry` to the database in one transaction (an advisory lock
keeps two syncs from interleaving) and writes one ``audit_events`` row per created / updated / deleted row,
with the before and after image. ``export_registry`` reads the database back into a :class:`Registry`; after a
sync of a clean database the two are equal, which ``tests/test_registry_sync.py`` checks.

Semantics (deliberate; the report and the audit log make every effect visible):

* sync applies the file: every provider, connection, unit, capability, route and budget it lists is set to
  the file's values, including ``connections.status``. That is how the owner flips an account from
  ``needs_login`` to ``active`` after adding its key, and it also means a re-sync reverts a pause done in
  the Console. ``dry_run=True`` computes the same report and rolls everything back.
* units, routes and budgets are pure configuration: those not listed in the file are deleted (for the
  connections / capabilities the file lists; budgets entirely).
* providers, connections and capabilities are never deleted by sync (they own history: quota, usage, runs).
  Rows present in the database but absent from the file are reported as ``orphans``.
* a route that the Console disabled is not part of the exported registry (the registry has no flag for it).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, LiteralString

from psycopg import AsyncConnection, sql
from psycopg.errors import UndefinedTable
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError

from farm.capabilities.schemas import CAPABILITY_MODELS
from farm.db.pool import DbPool
from farm.registry.loader import RegistryError
from farm.registry.models import (
    MCP_CAPABILITY_PREFIX,
    BudgetSpec,
    CapabilitySpec,
    ConnectionSpec,
    ProviderSpec,
    Registry,
    SettingsSpec,
    UnitSpec,
)

ADVISORY_LOCK_KEY = 0x46_41_52_4D  # "FARM"

type Row = dict[str, Any]
type Key = tuple[Any, ...]
type Conn = AsyncConnection[TupleRow]


class SyncReport(BaseModel):
    """What a sync did (or, for a dry run, would do), per table."""

    dry_run: bool = False
    created: dict[str, int] = Field(default_factory=dict)
    updated: dict[str, int] = Field(default_factory=dict)
    deleted: dict[str, int] = Field(default_factory=dict)
    unchanged: dict[str, int] = Field(default_factory=dict)
    orphans: dict[str, list[str]] = Field(default_factory=dict)
    """Rows in the database that the registry no longer lists (kept, because they own history)."""

    @property
    def changes(self) -> int:
        return sum(self.created.values()) + sum(self.updated.values()) + sum(self.deleted.values())


@dataclass(frozen=True)
class _Table:
    name: str
    keys: tuple[str, ...]
    columns: tuple[str, ...]
    json_columns: frozenset[str] = frozenset()

    @property
    def all_columns(self) -> tuple[str, ...]:
        return self.keys + self.columns


PROVIDERS = _Table(
    "providers",
    ("id",),
    ("name", "kind", "executor", "default_strategy", "enabled", "config"),
    frozenset({"config"}),
)
CONNECTIONS = _Table(
    "connections",
    ("id",),
    (
        "provider_id",
        "label",
        "auth_ref",
        "scope",
        "priority",
        "strategy",
        "concurrency",
        "rate_per_min",
        "status",
        "plan",
        "meta",
    ),
    frozenset({"plan", "meta"}),
)
UNITS = _Table(
    "consumption_units",
    ("connection_id", "unit"),
    ("limit_value", "period", "reset_anchor", "charged_on", "unit_cost_usd", "estimate_per_call"),
)
CAPABILITIES = _Table(
    "capabilities",
    ("name",),
    ("kind", "description", "input_schema", "output_schema", "default_strategy", "cache_ttl_seconds"),
    frozenset({"input_schema", "output_schema"}),
)
ROUTES = _Table("capability_routes", ("capability", "provider_id"), ("position", "enabled"))
BUDGETS = _Table("budgets", ("scope", "ref"), ("monthly_usd", "hard_stop"))
SETTINGS = _Table(
    "farm_settings", ("id",), ("owner_email", "global_monthly_budget_usd", "alert_thresholds", "timezone")
)


def _bump(bucket: dict[str, int], table: _Table) -> None:
    bucket[table.name] = bucket.get(table.name, 0) + 1


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _db_value(table: _Table, column: str, value: Any) -> Any:
    return Jsonb(_jsonable(value)) if column in table.json_columns else value


async def _reconcile(
    conn: Conn,
    report: SyncReport,
    actor: str,
    table: _Table,
    rows: Sequence[Row],
    *,
    prune: Callable[[Key], bool] | None = None,
) -> None:
    """Make ``table`` contain ``rows`` (and delete rows ``prune`` selects that are not in ``rows``)."""
    cols = table.all_columns
    cur = await conn.execute(
        sql.SQL("select {} from public.{}").format(
            sql.SQL(", ").join(map(sql.Identifier, cols)), sql.Identifier(table.name)
        )
    )
    existing: dict[Key, Row] = {}
    for values in await cur.fetchall():
        row = dict(zip(cols, values, strict=True))
        existing[tuple(row[k] for k in table.keys)] = row

    desired: dict[Key, Row] = {tuple(r[k] for k in table.keys): r for r in rows}
    for key, row in desired.items():
        before = existing.get(key)
        if before is None:
            await _insert(conn, table, row)
            await _audit(conn, actor, "registry.create", table, key, None, row)
            _bump(report.created, table)
        elif any(_differs(before[c], row[c]) for c in table.columns):
            await _update(conn, table, row)
            await _audit(conn, actor, "registry.update", table, key, before, row)
            _bump(report.updated, table)
        else:
            _bump(report.unchanged, table)

    if prune is not None:
        for key, before in existing.items():
            if key not in desired and prune(key):
                await _delete(conn, table, key)
                await _audit(conn, actor, "registry.delete", table, key, before, None)
                _bump(report.deleted, table)


def _differs(a: Any, b: Any) -> bool:
    return bool(_jsonable(a) != _jsonable(b))


async def _insert(conn: Conn, table: _Table, row: Row) -> None:
    cols = table.all_columns
    await conn.execute(
        sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table.name),
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        ),
        [_db_value(table, c, row[c]) for c in cols],
    )


async def _update(conn: Conn, table: _Table, row: Row) -> None:
    await conn.execute(
        sql.SQL("update public.{} set {} where {}").format(
            sql.Identifier(table.name),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in table.columns),
            sql.SQL(" and ").join(
                sql.SQL("{} is not distinct from %s").format(sql.Identifier(k)) for k in table.keys
            ),
        ),
        [*(_db_value(table, c, row[c]) for c in table.columns), *(row[k] for k in table.keys)],
    )


async def _delete(conn: Conn, table: _Table, key: Key) -> None:
    await conn.execute(
        sql.SQL("delete from public.{} where {}").format(
            sql.Identifier(table.name),
            sql.SQL(" and ").join(
                sql.SQL("{} is not distinct from %s").format(sql.Identifier(k)) for k in table.keys
            ),
        ),
        list(key),
    )


async def _audit(
    conn: Conn, actor: str, action: str, table: _Table, key: Key, before: Row | None, after: Row | None
) -> None:
    def image(row: Row | None) -> Jsonb | None:
        return None if row is None else Jsonb(_jsonable(row))

    target = f"{table.name}:{'/'.join('' if k is None else str(k) for k in key)}"
    await conn.execute(
        "insert into public.audit_events (actor, action, target, before, after) values (%s, %s, %s, %s, %s)",
        (actor, action, target, image(before), image(after)),
    )


# --- registry -> rows -------------------------------------------------------------------------------


def _provider_row(provider_id: str, spec: ProviderSpec) -> Row:
    return {
        "id": provider_id,
        "name": spec.name or provider_id,
        "kind": spec.kind,
        "executor": spec.executor,
        "default_strategy": spec.default_strategy,
        "enabled": spec.enabled,
        "config": spec.config,
    }


def _connection_row(provider_id: str, spec: ConnectionSpec) -> Row:
    return {
        "id": spec.id,
        "provider_id": provider_id,
        "label": spec.label,
        "auth_ref": spec.auth_ref,
        "scope": list(spec.scope),
        "priority": spec.priority,
        "strategy": spec.strategy,
        "concurrency": spec.concurrency,
        "rate_per_min": spec.rate_per_min,
        "status": spec.status,
        "plan": spec.plan.model_dump(mode="json") if spec.plan is not None else {},
        "meta": spec.meta,
    }


def _unit_row(connection_id: str, unit: str, spec: UnitSpec) -> Row:
    return {
        "connection_id": connection_id,
        "unit": unit,
        "limit_value": spec.limit,
        "period": spec.period,
        "reset_anchor": spec.anchor,
        "charged_on": spec.charged_on,
        "unit_cost_usd": spec.unit_cost_usd,
        "estimate_per_call": spec.estimate_per_call,
    }


def _capability_row(name: str, spec: CapabilitySpec) -> Row:
    models = CAPABILITY_MODELS.get(name)
    return {
        "name": name,
        "kind": spec.kind,
        "description": spec.description,
        "input_schema": models[0].model_json_schema() if models else {},
        "output_schema": models[1].model_json_schema() if models else {},
        "default_strategy": spec.strategy,
        "cache_ttl_seconds": spec.cache_ttl_seconds,
    }


def _budget_rows(budgets: BudgetSpec) -> list[Row]:
    rows = [{"scope": "global", "ref": None, "monthly_usd": budgets.global_monthly_usd}]
    rows += [
        {"scope": "provider", "ref": ref, "monthly_usd": usd} for ref, usd in budgets.per_provider.items()
    ]
    rows += [
        {"scope": "connection", "ref": ref, "monthly_usd": usd} for ref, usd in budgets.per_connection.items()
    ]
    return [{**row, "hard_stop": budgets.hard_stop} for row in rows]


def _settings_row(settings: SettingsSpec, budgets: BudgetSpec) -> Row:
    return {
        "id": 1,
        "owner_email": settings.owner_email,
        "global_monthly_budget_usd": budgets.global_monthly_usd,
        "alert_thresholds": list(settings.alert_thresholds),
        "timezone": settings.timezone,
    }


async def _orphans(conn: Conn, table: str, key: str, known: Iterable[str]) -> list[str]:
    cur = await conn.execute(
        sql.SQL("select {k} from public.{t} where not ({k} = any(%s)) order by {k}").format(
            k=sql.Identifier(key), t=sql.Identifier(table)
        ),
        (sorted(known),),
    )
    return [row[0] for row in await cur.fetchall()]


async def sync_registry(
    pool: DbPool, registry: Registry, *, actor: str = "registry-sync", dry_run: bool = False
) -> SyncReport:
    """Apply ``registry`` to the database atomically; see the module docstring for the semantics."""
    report = SyncReport(dry_run=dry_run)
    provider_rows = [_provider_row(pid, spec) for pid, spec in registry.providers.items()]
    connection_rows = [_connection_row(pid, c) for pid, c in registry.iter_connections()]
    unit_rows = [
        _unit_row(c.id, unit, spec) for _, c in registry.iter_connections() for unit, spec in c.units.items()
    ]
    capability_rows = [_capability_row(name, spec) for name, spec in registry.capabilities.items()]
    route_rows = [
        {"capability": name, "provider_id": provider_id, "position": position, "enabled": True}
        for name, spec in registry.capabilities.items()
        for position, provider_id in enumerate(spec.routes)
    ]
    connection_ids = {r["id"] for r in connection_rows}
    capability_names = set(registry.capabilities)

    async with pool.connection() as conn, conn.transaction(force_rollback=dry_run):
        await conn.execute("select pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
        await _reconcile(conn, report, actor, SETTINGS, [_settings_row(registry.settings, registry.budgets)])
        await _reconcile(conn, report, actor, PROVIDERS, provider_rows)
        await _reconcile(conn, report, actor, CONNECTIONS, connection_rows)
        await _reconcile(conn, report, actor, UNITS, unit_rows, prune=lambda key: key[0] in connection_ids)
        await _reconcile(conn, report, actor, CAPABILITIES, capability_rows)
        await _reconcile(
            conn, report, actor, ROUTES, route_rows, prune=lambda key: key[0] in capability_names
        )
        await _reconcile(conn, report, actor, BUDGETS, _budget_rows(registry.budgets), prune=lambda _: True)

        for table, key, known in (
            ("providers", "id", registry.providers),
            ("connections", "id", connection_ids),
            ("capabilities", "name", capability_names),
        ):
            missing = await _orphans(conn, table, key, known)
            if missing:
                report.orphans[table] = missing
    return report


# --- rows -> registry -------------------------------------------------------------------------------


async def _fetch(conn: Conn, query: LiteralString) -> list[Row]:
    cur = await conn.execute(query)
    names = [d.name for d in cur.description or ()]
    return [dict(zip(names, values, strict=True)) for values in await cur.fetchall()]


async def export_registry(pool: DbPool) -> Registry:
    """Read the database back into a :class:`Registry` (the inverse of ``sync_registry``).

    The export holds exactly what the database holds (the ``mcp:<provider>`` pass-through capabilities,
    which the MCP sync owns, are left out): nothing is added to it, so a sync of a registry file and an
    export of the result are equal. A blank Farm (no providers, no capabilities) exports as a blank
    registry; a database that was never migrated, or never seeded with its settings row, is a
    :class:`RegistryError`.
    """
    try:
        async with pool.connection() as conn:
            settings = await _fetch(conn, "select * from public.farm_settings where id = 1")
            providers = await _fetch(conn, "select * from public.providers order by id")
            connections = await _fetch(conn, "select * from public.connections order by priority, id")
            units = await _fetch(conn, "select * from public.consumption_units order by connection_id, unit")
            capabilities = await _fetch(conn, "select * from public.capabilities order by name")
            routes = await _fetch(
                conn, "select * from public.capability_routes where enabled order by capability, position"
            )
            budgets = await _fetch(conn, "select * from public.budgets order by scope, ref")
    except UndefinedTable:
        raise RegistryError(
            "the database has no Farm schema yet (run `farm db migrate`, then `farm registry sync`)"
        ) from None
    if not settings:
        raise RegistryError("the database holds no registry yet (run `farm registry sync`)")

    owner_email = settings[0]["owner_email"]
    if not owner_email:  # migrated but never synced: the owner is whoever the registry file names
        try:
            from farm.registry.loader import load_registry
            from farm.registry.writer import DEFAULT_REGISTRY_PATH

            owner_email = load_registry(DEFAULT_REGISTRY_PATH).settings.owner_email
        except Exception:
            owner_email = "owner@farm.local"

    units_by_connection: dict[str, dict[str, Row]] = defaultdict(dict)
    for u in units:
        units_by_connection[u["connection_id"]][u["unit"]] = {
            "limit": u["limit_value"],
            "period": u["period"],
            "anchor": u["reset_anchor"],
            "charged_on": u["charged_on"],
            "unit_cost_usd": u["unit_cost_usd"],
            "estimate_per_call": u["estimate_per_call"],
        }
    connections_by_provider: dict[str, list[Row]] = defaultdict(list)
    for c in connections:
        connections_by_provider[c["provider_id"]].append(
            {
                "id": c["id"],
                "label": c["label"] or c["id"],
                "auth_ref": c["auth_ref"],
                "scope": c["scope"],
                "priority": c["priority"],
                "strategy": c["strategy"],
                "concurrency": c["concurrency"],
                "rate_per_min": c["rate_per_min"],
                "status": c["status"],
                "plan": c["plan"] or None,
                "meta": c["meta"],
                "units": units_by_connection.get(c["id"], {}),
            }
        )
    routes_by_capability: dict[str, list[str]] = defaultdict(list)
    for r in routes:
        routes_by_capability[r["capability"]].append(r["provider_id"])

    caps_dict: dict[str, Any] = {
        c["name"]: {
            "kind": c["kind"],
            "description": c["description"],
            "routes": routes_by_capability.get(c["name"], []),
            "strategy": c["default_strategy"] or "failover",
            "cache_ttl_seconds": c["cache_ttl_seconds"],
        }
        for c in capabilities
        if not c["name"].startswith(MCP_CAPABILITY_PREFIX)
    }

    global_rows = [b for b in budgets if b["scope"] == "global"]
    data: Row = {
        "providers": {
            p["id"]: {
                "name": p["name"],
                "kind": p["kind"],
                "executor": p["executor"],
                "default_strategy": p["default_strategy"] or "failover",
                "enabled": p["enabled"],
                "config": p["config"],
                "connections": connections_by_provider.get(p["id"], []),
            }
            for p in providers
        },
        "capabilities": caps_dict,
        "budgets": {
            "global_monthly_usd": global_rows[0]["monthly_usd"] if global_rows else 0,
            "per_provider": {b["ref"]: b["monthly_usd"] for b in budgets if b["scope"] == "provider"},
            "per_connection": {b["ref"]: b["monthly_usd"] for b in budgets if b["scope"] == "connection"},
            "hard_stop": global_rows[0]["hard_stop"] if global_rows else True,
        },
        "settings": {
            "owner_email": owner_email,
            "alert_thresholds": settings[0]["alert_thresholds"],
            "timezone": settings[0]["timezone"],
            "global_monthly_budget_usd": settings[0]["global_monthly_budget_usd"],
        },
    }
    try:
        return Registry.model_validate(data)
    except ValidationError as exc:
        raise RegistryError(
            f"the database registry is inconsistent: {exc.error_count()} validation error(s); "
            + "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors(include_url=False, include_input=False, include_context=False)
            )
        ) from None
