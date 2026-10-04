"""Farm commands consumer and executor.

Processes commands queued in `farm_commands` by the Console or admin tools.
Validates payloads using Pydantic per command kind.
Executes DB updates, emits audit_events, updates command status to 'done' or 'rejected'.
Rejects unknown kinds, invalid payloads, or raw secrets in auth_ref.
"""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import structlog
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from farm.db.pool import DbPool
from farm.executors.base import ConnectionView
from farm.registry.models import STRATEGIES
from farm.secrets import AuthRefError, resolve_auth

log = structlog.get_logger(__name__)

# Pattern to detect raw secret keys or tokens in auth_ref
_SECRET_PATTERNS = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-or-v1-[0-9a-f]{10,}|ghp_[A-Za-z0-9]{10,}|"
    r"AKIA[0-9A-Z]{10,}|sk-proj-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{20,}|Bearer\s+)",
    re.IGNORECASE,
)


def validate_auth_ref(val: str) -> str:
    """Validate auth_ref format: must be env:NAME, token-store:ID, or cli:PROFILE, never a raw secret."""
    text = val.strip()
    valid_prefixes = ("env:", "token-store:", "cli:")
    if not any(text.startswith(p) for p in valid_prefixes):
        raise ValueError(
            f"auth_ref must start with one of {valid_prefixes}, got '{text[:20]}...'"
        )

    # Check for raw secret shapes or suspicious characters
    rest = text.split(":", 1)[1]
    if _SECRET_PATTERNS.search(text) or _SECRET_PATTERNS.search(rest):
        raise ValueError("auth_ref appears to contain a raw secret key instead of a reference")

    if text.startswith("env:"):
        # Env var name should be alphanumeric + underscores
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", rest):
            raise ValueError(f"Invalid environment variable name in auth_ref: '{rest}'")

    return text


# --- Payload Models ---


class PausePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str
    reason: str | None = None


class ResumePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str


class SetPriorityPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str
    priority: int = Field(ge=0)


class SetStrategyPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    strategy: str
    provider_id: str | None = None
    connection_id: str | None = None
    capability: str | None = None

    @field_validator("strategy")
    @classmethod
    def check_strategy(cls, v: str) -> str:
        if v not in STRATEGIES:
            raise ValueError(f"Unknown strategy '{v}'. Allowed: {STRATEGIES}")
        return v


class SetBudgetPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scope: Literal["global", "provider", "connection"]
    monthly_usd: Decimal = Field(ge=0)
    ref: str | None = None
    hard_stop: bool = True

    @field_validator("ref")
    @classmethod
    def check_ref(cls, v: str | None, info: Any) -> str | None:
        scope = info.data.get("scope")
        if scope in ("provider", "connection") and not v:
            raise ValueError(f"ref is required when budget scope is '{scope}'")
        return v


class AddConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider_id: str
    id: str
    auth_ref: str
    label: str | None = None
    scope: list[str] = Field(default_factory=lambda: ["internal"])
    priority: int = 100
    strategy: str | None = None
    concurrency: int = 1
    rate_per_min: int | None = None
    status: Literal["active", "paused", "needs_login", "exhausted", "disabled"] = "active"
    plan: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    units: dict[str, Any] = Field(default_factory=dict)

    @field_validator("auth_ref")
    @classmethod
    def check_auth_ref(cls, v: str) -> str:
        return validate_auth_ref(v)


class UpdateConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str
    label: str | None = None
    auth_ref: str | None = None
    scope: list[str] | None = None
    priority: int | None = None
    strategy: str | None = None
    concurrency: int | None = None
    rate_per_min: int | None = None
    status: Literal["active", "paused", "needs_login", "exhausted", "disabled"] | None = None
    plan: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    @field_validator("auth_ref")
    @classmethod
    def check_auth_ref(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_auth_ref(v)
        return v


class RemoveConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str


class SetRoutePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capability: str
    provider_id: str
    position: int = 0
    enabled: bool = True


class TestConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    connection_id: str
    capability: str | None = None


class AckAlertPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    alert_id: UUID | str


PAYLOAD_VALIDATORS: dict[str, type[BaseModel]] = {
    "pause": PausePayload,
    "resume": ResumePayload,
    "set_priority": SetPriorityPayload,
    "set_strategy": SetStrategyPayload,
    "set_budget": SetBudgetPayload,
    "add_connection": AddConnectionPayload,
    "update_connection": UpdateConnectionPayload,
    "remove_connection": RemoveConnectionPayload,
    "set_route": SetRoutePayload,
    "test_connection": TestConnectionPayload,
    "ack_alert": AckAlertPayload,
}


async def _emit_audit(
    conn: Any,
    actor: str,
    action: str,
    target: str,
    before: Any = None,
    after: Any = None,
) -> None:
    await conn.execute(
        "insert into public.audit_events (actor, action, target, before, after) values (%s, %s, %s, %s, %s)",
        (
            actor,
            action,
            target,
            Jsonb(before) if before is not None else None,
            Jsonb(after) if after is not None else None,
        ),
    )


async def execute_command(
    pool: DbPool,
    cmd_id: UUID | str,
    kind: str,
    payload_raw: dict[str, Any],
    actor: str = "console",
) -> tuple[str, dict[str, Any]]:
    """Execute a single command against the database.

    Returns (status, result_dict) where status is 'done' or 'rejected'.
    """
    cid = cmd_id if isinstance(cmd_id, UUID) else UUID(str(cmd_id))

    validator = PAYLOAD_VALIDATORS.get(kind)
    if validator is None:
        reason = f"Unknown command kind '{kind}'"
        log.warning("command.rejected", cmd_id=str(cid), reason=reason)
        return "rejected", {"error": reason}

    try:
        payload = validator.model_validate(payload_raw)
    except ValidationError as err:
        errors = "; ".join(f"{e['loc']}: {e['msg']}" for e in err.errors())
        reason = f"Invalid payload for '{kind}': {errors}"
        log.warning("command.rejected", cmd_id=str(cid), reason=reason)
        return "rejected", {"error": reason}
    except Exception as exc:
        reason = f"Validation failed: {exc}"
        log.warning("command.rejected", cmd_id=str(cid), reason=reason)
        return "rejected", {"error": reason}

    async with pool.connection() as conn:
        before: dict[str, Any] | None = None
        after: dict[str, Any] | None = None
        try:
            if kind == "pause":
                assert isinstance(payload, PausePayload)
                cur = await conn.execute(
                    "select status from public.connections where id = %s",
                    (payload.connection_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}
                before = {"status": row[0]}
                await conn.execute(
                    "update public.connections set status = 'paused' where id = %s",
                    (payload.connection_id,),
                )
                after = {"status": "paused"}
                await _emit_audit(conn, actor, "pause", payload.connection_id, before, after)
                return "done", {"connection_id": payload.connection_id, "status": "paused"}

            elif kind == "resume":
                assert isinstance(payload, ResumePayload)
                cur = await conn.execute(
                    "select status from public.connections where id = %s",
                    (payload.connection_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}
                before = {"status": row[0]}
                await conn.execute(
                    "update public.connections set status = 'active' where id = %s",
                    (payload.connection_id,),
                )
                after = {"status": "active"}
                await _emit_audit(conn, actor, "resume", payload.connection_id, before, after)
                return "done", {"connection_id": payload.connection_id, "status": "active"}

            elif kind == "set_priority":
                assert isinstance(payload, SetPriorityPayload)
                cur = await conn.execute(
                    "select priority from public.connections where id = %s",
                    (payload.connection_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}
                before = {"priority": row[0]}
                await conn.execute(
                    "update public.connections set priority = %s where id = %s",
                    (payload.priority, payload.connection_id),
                )
                after = {"priority": payload.priority}
                await _emit_audit(conn, actor, "set_priority", payload.connection_id, before, after)
                return "done", {"connection_id": payload.connection_id, "priority": payload.priority}

            elif kind == "set_strategy":
                assert isinstance(payload, SetStrategyPayload)
                if not any((payload.provider_id, payload.connection_id, payload.capability)):
                    return "rejected", {
                        "error": "At least one of provider_id, connection_id, or capability must be specified"
                    }
                target = payload.connection_id or payload.provider_id or payload.capability or ""
                before = {}
                after = {"strategy": payload.strategy}

                if payload.connection_id:
                    cur = await conn.execute(
                        "select strategy from public.connections where id = %s",
                        (payload.connection_id,),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}
                    before["strategy"] = row[0]
                    await conn.execute(
                        "update public.connections set strategy = %s where id = %s",
                        (payload.strategy, payload.connection_id),
                    )

                if payload.provider_id:
                    cur = await conn.execute(
                        "select default_strategy from public.providers where id = %s",
                        (payload.provider_id,),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return "rejected", {"error": f"Provider '{payload.provider_id}' not found"}
                    before["default_strategy"] = row[0]
                    await conn.execute(
                        "update public.providers set default_strategy = %s where id = %s",
                        (payload.strategy, payload.provider_id),
                    )

                if payload.capability:
                    cur = await conn.execute(
                        "select default_strategy from public.capabilities where name = %s",
                        (payload.capability,),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return "rejected", {"error": f"Capability '{payload.capability}' not found"}
                    before["default_strategy"] = row[0]
                    await conn.execute(
                        "update public.capabilities set default_strategy = %s where name = %s",
                        (payload.strategy, payload.capability),
                    )

                await _emit_audit(conn, actor, "set_strategy", target, before, after)
                return "done", {"target": target, "strategy": payload.strategy}

            elif kind == "set_budget":
                assert isinstance(payload, SetBudgetPayload)
                target = f"{payload.scope}:{payload.ref or 'global'}"
                cur = await conn.execute(
                    "select id, monthly_usd, hard_stop from public.budgets "
                    "where scope = %s and ref is not distinct from %s::text",
                    (payload.scope, payload.ref),
                )
                row = await cur.fetchone()
                before = {"monthly_usd": float(row[1]), "hard_stop": row[2]} if row else None

                if row:
                    await conn.execute(
                        "update public.budgets set monthly_usd = %s, hard_stop = %s where id = %s",
                        (payload.monthly_usd, payload.hard_stop, row[0]),
                    )
                else:
                    await conn.execute(
                        "insert into public.budgets (scope, ref, monthly_usd, hard_stop) "
                        "values (%s, %s, %s, %s)",
                        (payload.scope, payload.ref, payload.monthly_usd, payload.hard_stop),
                    )

                if payload.scope == "global":
                    await conn.execute(
                        "update public.farm_settings set global_monthly_budget_usd = %s where id = 1",
                        (payload.monthly_usd,),
                    )

                after = {"monthly_usd": float(payload.monthly_usd), "hard_stop": payload.hard_stop}
                await _emit_audit(conn, actor, "set_budget", target, before, after)
                return "done", {
                    "scope": payload.scope,
                    "ref": payload.ref,
                    "monthly_usd": float(payload.monthly_usd),
                    "hard_stop": payload.hard_stop,
                }

            elif kind == "add_connection":
                assert isinstance(payload, AddConnectionPayload)
                cur_p = await conn.execute(
                    "select id from public.providers where id = %s",
                    (payload.provider_id,),
                )
                if await cur_p.fetchone() is None:
                    return "rejected", {"error": f"Provider '{payload.provider_id}' does not exist"}

                cur_c = await conn.execute(
                    "select id from public.connections where id = %s",
                    (payload.id,),
                )
                if await cur_c.fetchone() is not None:
                    return "rejected", {"error": f"Connection '{payload.id}' already exists"}

                await conn.execute(
                    "insert into public.connections "
                    "(id, provider_id, label, auth_ref, scope, priority, strategy, "
                    "concurrency, rate_per_min, status, plan, meta) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        payload.id,
                        payload.provider_id,
                        payload.label,
                        payload.auth_ref,
                        payload.scope,
                        payload.priority,
                        payload.strategy,
                        payload.concurrency,
                        payload.rate_per_min,
                        payload.status,
                        Jsonb(payload.plan),
                        Jsonb(payload.meta),
                    ),
                )

                for unit_name, unit_spec in payload.units.items():
                    spec = unit_spec if isinstance(unit_spec, dict) else {"limit": unit_spec}
                    await conn.execute(
                        "insert into public.consumption_units "
                        "(connection_id, unit, limit_value, period, reset_anchor, "
                        "charged_on, unit_cost_usd, estimate_per_call) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            payload.id,
                            unit_name,
                            spec.get("limit"),
                            spec.get("period", "month"),
                            spec.get("anchor"),
                            spec.get("charged_on", "attempt"),
                            spec.get("unit_cost_usd", 0),
                            spec.get("estimate_per_call", 1),
                        ),
                    )

                after = payload.model_dump(mode="json")
                await _emit_audit(conn, actor, "add_connection", payload.id, None, after)
                return "done", {"connection_id": payload.id, "status": "created"}

            elif kind == "update_connection":
                assert isinstance(payload, UpdateConnectionPayload)
                cur = await conn.execute(
                    "select label, auth_ref, scope, priority, strategy, concurrency, "
                    "rate_per_min, status, plan, meta "
                    "from public.connections where id = %s",
                    (payload.connection_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}

                before = {
                    "label": row[0],
                    "auth_ref": row[1],
                    "scope": row[2],
                    "priority": row[3],
                    "strategy": row[4],
                    "concurrency": row[5],
                    "rate_per_min": row[6],
                    "status": row[7],
                    "plan": row[8],
                    "meta": row[9],
                }

                updates: dict[str, Any] = {}
                for field in (
                    "label",
                    "auth_ref",
                    "scope",
                    "priority",
                    "strategy",
                    "concurrency",
                    "rate_per_min",
                    "status",
                ):
                    val = getattr(payload, field)
                    if val is not None:
                        updates[field] = val

                if payload.plan is not None:
                    updates["plan"] = Jsonb(payload.plan)
                if payload.meta is not None:
                    updates["meta"] = Jsonb(payload.meta)

                if updates:
                    set_clauses = [f"{k} = %s" for k in updates]
                    values = list(updates.values()) + [payload.connection_id]
                    await conn.execute(
                        f"update public.connections set {', '.join(set_clauses)} where id = %s",
                        values,
                    )

                after = {**before, **{k: getattr(payload, k) for k in updates if hasattr(payload, k)}}
                await _emit_audit(conn, actor, "update_connection", payload.connection_id, before, after)
                return "done", {"connection_id": payload.connection_id, "updated": list(updates.keys())}

            elif kind == "remove_connection":
                assert isinstance(payload, RemoveConnectionPayload)
                cur = await conn.execute(
                    "select id, provider_id from public.connections where id = %s",
                    (payload.connection_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}

                before = {"id": row[0], "provider_id": row[1]}
                await conn.execute(
                    "delete from public.connections where id = %s",
                    (payload.connection_id,),
                )
                await _emit_audit(conn, actor, "remove_connection", payload.connection_id, before, None)
                return "done", {"connection_id": payload.connection_id, "status": "deleted"}

            elif kind == "set_route":
                assert isinstance(payload, SetRoutePayload)
                cur_cap = await conn.execute(
                    "select name from public.capabilities where name = %s",
                    (payload.capability,),
                )
                if await cur_cap.fetchone() is None:
                    return "rejected", {"error": f"Capability '{payload.capability}' not found"}

                cur_p = await conn.execute(
                    "select id from public.providers where id = %s",
                    (payload.provider_id,),
                )
                if await cur_p.fetchone() is None:
                    return "rejected", {"error": f"Provider '{payload.provider_id}' not found"}

                target = f"{payload.capability}:{payload.provider_id}"
                await conn.execute(
                    "insert into public.capability_routes (capability, provider_id, position, enabled) "
                    "values (%s, %s, %s, %s) "
                    "on conflict (capability, provider_id) do update set "
                    "position = excluded.position, enabled = excluded.enabled",
                    (payload.capability, payload.provider_id, payload.position, payload.enabled),
                )
                after = payload.model_dump(mode="json")
                await _emit_audit(conn, actor, "set_route", target, None, after)
                return "done", {"route": target, "position": payload.position, "enabled": payload.enabled}

            elif kind == "test_connection":
                assert isinstance(payload, TestConnectionPayload)
                cur = await conn.execute(
                    "select id, provider_id, auth_ref, meta, concurrency, rate_per_min "
                    "from public.connections where id = %s",
                    (payload.connection_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}

                conn_view = ConnectionView(
                    id=row[0],
                    provider_id=row[1],
                    auth_ref=row[2],
                    meta=row[3] or {},
                    concurrency=row[4] or 1,
                    rate_per_min=row[5],
                )

                try:
                    resolve_auth(conn_view.auth_ref)
                    auth_ok = True
                    auth_err = None
                except AuthRefError as aerr:
                    auth_ok = False
                    auth_err = str(aerr)

                result_data = {
                    "connection_id": payload.connection_id,
                    "auth_ok": auth_ok,
                    "auth_error": auth_err,
                }
                await _emit_audit(
                    conn,
                    actor,
                    "test_connection",
                    payload.connection_id,
                    None,
                    {"result": result_data},
                )
                return "done", result_data

            elif kind == "ack_alert":
                assert isinstance(payload, AckAlertPayload)
                aid = payload.alert_id if isinstance(payload.alert_id, UUID) else UUID(str(payload.alert_id))
                cur = await conn.execute(
                    "update public.alerts set acked_at = now() "
                    "where id = %s and acked_at is null returning id",
                    (aid,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Alert '{aid}' not found or already acknowledged"}

                await _emit_audit(conn, actor, "ack_alert", str(aid), None, {"acked_at": "now()"})
                return "done", {"alert_id": str(aid), "status": "acknowledged"}

            return "rejected", {"error": f"Unhandled kind '{kind}'"}

        except Exception as exc:
            log.exception("command.execution_failed", cmd_id=str(cid), error=str(exc))
            return "failed", {"error": f"Execution exception: {exc}"}


async def process_command(pool: DbPool, command_id: UUID | str) -> tuple[str, dict[str, Any]]:
    """Fetch queued command, execute it, record result, and mark it done/rejected/failed."""
    cid = command_id if isinstance(command_id, UUID) else UUID(str(command_id))

    async with pool.connection() as conn:
        cur = await conn.execute(
            "select kind, payload, coalesce(created_by, 'system') from public.farm_commands "
            "where id = %s for update",
            (cid,),
        )
        row = await cur.fetchone()
        if row is None:
            return "rejected", {"error": "Command not found"}

        kind, payload_raw, created_by = row[0], row[1] or {}, row[2]
        await conn.execute(
            "update public.farm_commands set status = 'running' where id = %s",
            (cid,),
        )

    status, result = await execute_command(pool, cid, kind, payload_raw, actor=created_by)

    async with pool.connection() as conn:
        await conn.execute(
            "update public.farm_commands set status = %s, result = %s, done_at = now() where id = %s",
            (status, Jsonb(result), cid),
        )

    log.info("command.processed", cmd_id=str(cid), kind=kind, status=status)
    return status, result


async def poll_and_execute_queued(pool: DbPool, limit: int = 10) -> int:
    """Find queued commands and execute them. Returns number of commands processed."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select id from public.farm_commands where status = 'queued' "
            "order by created_at asc limit %s for update skip locked",
            (limit,),
        )
        rows = await cur.fetchall()

    count = 0
    for row in rows:
        cid = row[0]
        await process_command(pool, cid)
        count += 1
    return count


class CommandConsumer:
    """Background polling worker for farm_commands."""

    def __init__(self, pool: DbPool, poll_interval_s: float = 2.0) -> None:
        self.pool = pool
        self.poll_interval_s = poll_interval_s
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("command_consumer.started", interval_s=self.poll_interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("command_consumer.stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await poll_and_execute_queued(self.pool)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("command_consumer.poll_error", error=str(exc))

            try:
                await asyncio.sleep(self.poll_interval_s)
            except asyncio.CancelledError:
                break
