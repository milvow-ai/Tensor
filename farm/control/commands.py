"""Farm commands consumer and executor.

Processes commands queued in `farm_commands` by the Console or admin tools.
Validates payloads using Pydantic per command kind.
Executes DB updates, emits audit_events, updates command status to 'done' or 'rejected'.
Rejects unknown kinds, invalid payloads, or raw secrets in auth_ref.
"""

from __future__ import annotations

import asyncio
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import structlog
from psycopg.types.json import Jsonb
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from farm.db.pool import DbPool
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest
from farm.registry.models import (
    ExecutorKind,
    McpAuth,
    McpExpose,
    McpProviderSpec,
    ProviderKind,
    Strategy,
    looks_like_secret,
)
from farm.secrets import AuthRefError, redact, resolve_auth, resolve_token_store
from farm.settings import data_dir

log = structlog.get_logger(__name__)

SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, max_length=63)]

Period = Literal["minute", "hour", "day", "week", "month", "rolling_5h", "total", "none"]
ChargedOn = Literal["attempt", "success", "found"]


class CommandUnitSpec(BaseModel):
    """Specification of a consumption unit for a connection."""

    model_config = ConfigDict(extra="forbid")
    limit: float | None = Field(default=None, ge=0)
    period: Period = "month"
    anchor: int | None = Field(default=None, ge=1, le=31)
    charged_on: ChargedOn = "attempt"
    unit_cost_usd: float = Field(default=0.0, ge=0)
    estimate_per_call: float = Field(default=1.0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_primitive(cls, data: Any) -> Any:
        if isinstance(data, (int, float, Decimal)):
            return {"limit": float(data)}
        return data

    @model_validator(mode="after")
    def _check_anchor(self) -> CommandUnitSpec:
        if self.anchor is not None and self.period != "month":
            raise ValueError("anchor (day of month) only applies to period 'month'")
        return self


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
        raise ValueError(f"auth_ref must start with one of {valid_prefixes}, got '{text[:20]}...'")

    # Check for raw secret shapes or suspicious characters
    rest = text.split(":", 1)[1]
    if _SECRET_PATTERNS.search(text) or _SECRET_PATTERNS.search(rest):
        raise ValueError("auth_ref appears to contain a raw secret key instead of a reference")

    if text.startswith("env:"):
        # Env var name should be alphanumeric + underscores
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", rest):
            raise ValueError(f"Invalid environment variable name in auth_ref: '{rest}'")

    return text


def _assert_no_raw_secrets(obj: Any) -> None:
    """Recursively verify that no raw secrets are embedded in payload data."""
    if isinstance(obj, str):
        if looks_like_secret(obj):
            raise ValueError(
                f"Raw secret values are rejected: {redact(obj)}. "
                "Keep credentials in .env and use env:NAME references."
            )
        if obj.startswith("env:"):
            rest = obj.split(":", 1)[1]
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", rest):
                raise ValueError(f"Invalid environment variable name in reference: '{rest}'")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and looks_like_secret(k):
                raise ValueError("Secret-looking string in key rejected.")
            _assert_no_raw_secrets(v)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            _assert_no_raw_secrets(item)


# --- Payload Models ---


class PausePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: Slug
    reason: str | None = None


class ResumePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: Slug


class SetPriorityPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: Slug
    priority: int = Field(ge=0)


class SetStrategyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Strategy
    provider_id: Slug | None = None
    connection_id: Slug | None = None
    capability: Slug | None = None


class SetBudgetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["global", "provider", "connection"]
    monthly_usd: Decimal = Field(ge=0)
    ref: Slug | None = None
    hard_stop: bool = True

    @field_validator("ref")
    @classmethod
    def check_ref(cls, v: str | None, info: Any) -> str | None:
        scope = info.data.get("scope")
        if scope in ("provider", "connection") and not v:
            raise ValueError(f"ref is required when budget scope is '{scope}'")
        return v


class AddConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: Slug
    id: Slug
    auth_ref: str
    label: str | None = None
    scope: list[str] = Field(default_factory=lambda: ["internal"])
    priority: int = Field(default=100, ge=0)
    strategy: Strategy | None = None
    concurrency: int = Field(default=1, ge=1)
    rate_per_min: int | None = Field(default=None, ge=1)
    status: Literal["active", "paused", "needs_login", "exhausted", "disabled"] = "active"
    plan: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    units: dict[Slug, CommandUnitSpec] = Field(default_factory=dict)

    @field_validator("auth_ref")
    @classmethod
    def check_auth_ref(cls, v: str) -> str:
        return validate_auth_ref(v)


class UpdateConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: Slug
    label: str | None = None
    auth_ref: str | None = None
    scope: list[str] | None = None
    priority: int | None = Field(default=None, ge=0)
    strategy: Strategy | None = None
    concurrency: int | None = Field(default=None, ge=1)
    rate_per_min: int | None = Field(default=None, ge=1)
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
    model_config = ConfigDict(extra="forbid")
    connection_id: Slug


class SetRoutePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Slug
    provider_id: Slug
    position: int = 0
    enabled: bool = True


class TestConnectionPayload(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")
    connection_id: Slug
    capability: Slug | None = None


class AckAlertPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_id: UUID | str


class CancelAiJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: UUID | str


class SetMaxParallelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: Slug | None = None
    provider_id: Slug | None = None
    max_parallel: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def check_target(self) -> SetMaxParallelPayload:
        if not self.connection_id and not self.provider_id:
            raise ValueError("At least one of connection_id or provider_id must be specified")
        return self


class SetMcpToolAccessPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: Slug
    tool: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    access: Literal["allow", "deny"] | None = None

    @model_validator(mode="after")
    def resolve_access(self) -> SetMcpToolAccessPayload:
        if self.access is not None:
            self.enabled = self.access == "allow"
        return self


class SyncMcpToolsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: Slug | None = None


class AddProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Slug
    name: str | None = None
    kind: ProviderKind = "tool"
    executor: ExecutorKind = "mcp"
    default_strategy: Strategy = "failover"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    # MCP options (nested block or shorthand fields)
    mcp: dict[str, Any] | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | list[str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    auth: McpAuth = "none"
    namespace: Slug | None = None
    exposure: McpExpose = "auto"
    timeout_s: float | None = None

    # AI CLI options
    cli: Literal["claude", "codex", "gemini", "agy", "hermes"] | None = None
    account_id: Slug | None = None
    label: str | None = None
    models: list[str] = Field(default_factory=list)
    max_parallel: int = Field(default=1, ge=1)

    # OpenAPI options
    spec: str | None = None
    auth_env: str | None = None

    # First connection options
    connection_id: Slug | None = None
    auth_ref: str | None = None
    priority: int = Field(default=100, ge=0)
    concurrency: int = Field(default=1, ge=1)
    rate_per_min: int | None = Field(default=None, ge=1)
    status: Literal["active", "paused", "needs_login", "exhausted", "disabled"] | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    units: dict[Slug, CommandUnitSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_no_secrets(self) -> AddProviderPayload:
        _assert_no_raw_secrets(self.model_dump())
        return self


class UpdateProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Slug
    name: str | None = None
    enabled: bool | None = None
    default_strategy: Strategy | None = None
    config: dict[str, Any] | None = None
    mcp: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_no_secrets(self) -> UpdateProviderPayload:
        _assert_no_raw_secrets(self.model_dump())
        return self


class RemoveProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: Slug
    force: bool = False


_ACTIVE_GATEWAYS: list[tuple[Any, Any]] = []


def register_active_gateway(server: Any, ctx: Any) -> None:
    """Register an active FastMCP gateway server for dynamic tool refreshing."""
    entry = (server, ctx)
    if entry not in _ACTIVE_GATEWAYS:
        _ACTIVE_GATEWAYS.append(entry)


def unregister_active_gateway(server: Any, ctx: Any) -> None:
    """Unregister an active FastMCP gateway server."""
    entry = (server, ctx)
    if entry in _ACTIVE_GATEWAYS:
        _ACTIVE_GATEWAYS.remove(entry)


async def refresh_active_gateways() -> None:
    """Refresh MCP tools across all active gateway servers without restarting."""
    from farm.gateway.mcp_tools import register_mcp_tools

    for server, ctx in list(_ACTIVE_GATEWAYS):
        try:
            await register_mcp_tools(server, ctx)
        except Exception as exc:
            log.warning("gateway.refresh_failed", error=str(exc))


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
    "cancel_ai_job": CancelAiJobPayload,
    "set_max_parallel": SetMaxParallelPayload,
    "set_mcp_tool_access": SetMcpToolAccessPayload,
    "sync_mcp_tools": SyncMcpToolsPayload,
    "add_provider": AddProviderPayload,
    "update_provider": UpdateProviderPayload,
    "remove_provider": RemoveProviderPayload,
}


def export_command_schemas() -> dict[str, dict[str, Any]]:
    """Export JSON Schema for each command payload kind."""
    return {kind: validator.model_json_schema() for kind, validator in PAYLOAD_VALIDATORS.items()}


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


async def execute_provider_command(
    pool: DbPool,
    kind: str,
    payload: AddProviderPayload | UpdateProviderPayload | RemoveProviderPayload | dict[str, Any],
    actor: str = "console",
    registry_path: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Shared provider command executor for Console commands and CLI.

    Handles 'add_provider', 'update_provider', and 'remove_provider'.
    Validates with registry models, rejects literal secrets, syncs DB, writes registry file,
    emits audit events, and runs OPEN1 tool sync for MCP servers.
    """
    from farm.registry.writer import write_registry_file

    if kind == "add_provider":
        if isinstance(payload, dict):
            try:
                cmd = AddProviderPayload.model_validate(payload)
            except ValidationError as err:
                return "rejected", {"error": f"Invalid add_provider payload: {err}"}
        else:
            assert isinstance(payload, AddProviderPayload)
            cmd = payload

        # Determine provider characteristics
        provider_id = cmd.provider_id
        is_ai = cmd.kind == "ai" or cmd.cli is not None or cmd.executor == "cli_agent"
        is_openapi = cmd.spec is not None or (cmd.executor == "api" and not is_ai)
        is_mcp = (
            (cmd.executor == "mcp" or cmd.mcp is not None or cmd.command is not None or cmd.url is not None)
            and not is_ai
            and not is_openapi
        )

        async with pool.connection() as conn:
            cur = await conn.execute(
                "select id, kind, executor, config from public.providers where id = %s",
                (provider_id,),
            )
            existing_provider = await cur.fetchone()

        if is_ai:
            driver = cmd.cli or provider_id
            account_id = cmd.account_id or cmd.connection_id or f"{provider_id}-01"
            label = cmd.label or account_id

            # Verify connection does not already exist
            async with pool.connection() as conn:
                cur_c = await conn.execute(
                    "select id from public.connections where id = %s",
                    (account_id,),
                )
                if await cur_c.fetchone() is not None:
                    return "rejected", {"error": f"Connection '{account_id}' already exists"}

            # Meta and config dir per AI driver
            meta = dict(cmd.meta)
            meta["cli"] = driver
            if cmd.models:
                meta["models"] = cmd.models
            meta["max_parallel"] = cmd.max_parallel

            ai_data_dir = data_dir() / "ai" / account_id
            if driver in ("claude", "codex"):
                meta.setdefault("config_dir", str(ai_data_dir))
            elif driver in ("gemini", "agy"):
                meta.setdefault("home", str(ai_data_dir))
            elif driver == "hermes":
                meta.setdefault("profile", f"farm-{account_id}")

            status = cmd.status or "needs_login"
            auth_ref = cmd.auth_ref or f"cli:{account_id}"

            async with pool.connection() as conn:
                if existing_provider is None:
                    await conn.execute(
                        "insert into public.providers "
                        "(id, name, kind, executor, default_strategy, enabled, config) "
                        "values (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            provider_id,
                            cmd.name or provider_id.capitalize(),
                            "ai",
                            "cli_agent",
                            cmd.default_strategy,
                            cmd.enabled,
                            Jsonb(cmd.config),
                        ),
                    )
                await conn.execute(
                    "insert into public.connections (id, provider_id, label, auth_ref, scope, priority, "
                    "strategy, concurrency, rate_per_min, status, plan, meta) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        account_id,
                        provider_id,
                        label,
                        auth_ref,
                        ["internal"],
                        cmd.priority,
                        None,
                        cmd.max_parallel,
                        cmd.rate_per_min,
                        status,
                        Jsonb(cmd.plan),
                        Jsonb(meta),
                    ),
                )
                # Ensure capability routes for ask_ai and agent_task
                for cap_name in ("ask_ai", "agent_task"):
                    cur_cap = await conn.execute(
                        "select name from public.capabilities where name = %s",
                        (cap_name,),
                    )
                    if await cur_cap.fetchone() is not None:
                        cur_pos = await conn.execute(
                            "select coalesce(max(position), -1) "
                            "from public.capability_routes where capability = %s",
                            (cap_name,),
                        )
                        pos_row = await cur_pos.fetchone()
                        max_pos = int(pos_row[0]) if pos_row is not None else -1
                        await conn.execute(
                            "insert into public.capability_routes "
                            "(capability, provider_id, position, enabled) "
                            "values (%s, %s, %s, true) on conflict (capability, provider_id) do nothing",
                            (cap_name, provider_id, max_pos + 1),
                        )
                await _emit_audit(
                    conn,
                    actor,
                    "add_provider",
                    provider_id,
                    None,
                    {"account_id": account_id, "kind": "ai", "status": status},
                )
                await conn.commit()

            await write_registry_file(pool, registry_path)
            next_step = f"farm ai login {account_id}"
            return "done", {
                "provider_id": provider_id,
                "connection_id": account_id,
                "status": status,
                "next_step": next_step,
            }

        elif is_mcp:
            if existing_provider is not None:
                return "rejected", {"error": f"Provider '{provider_id}' already exists"}

            # Build MCP spec
            if cmd.mcp is not None:
                mcp_raw = dict(cmd.mcp)
            else:
                transport = "stdio" if cmd.command else ("http" if cmd.url else "stdio")
                if transport == "stdio":
                    if not cmd.command:
                        return "rejected", {"error": "stdio MCP server requires command"}
                    env_dict = {}
                    if isinstance(cmd.env, list):
                        for e in cmd.env:
                            env_dict[str(e)] = f"env:{e}"
                    elif isinstance(cmd.env, dict):
                        for k, v in cmd.env.items():
                            env_dict[str(k)] = v if v.startswith("env:") else f"env:{v}"
                    mcp_raw = {
                        "transport": "stdio",
                        "command": cmd.command,
                        "args": cmd.args,
                        "cwd": cmd.cwd,
                        "env": env_dict,
                        "auth": "none",
                    }
                else:
                    if not cmd.url:
                        return "rejected", {"error": "http MCP server requires url"}
                    headers_dict = {}
                    for k, v in cmd.headers.items():
                        headers_dict[str(k)] = v if v.startswith("env:") else f"env:{v}"
                    mcp_raw = {
                        "transport": "http",
                        "url": cmd.url,
                        "headers": headers_dict,
                        "auth": cmd.auth,
                    }
                if cmd.namespace:
                    mcp_raw["namespace"] = cmd.namespace
                if cmd.exposure:
                    mcp_raw["expose"] = cmd.exposure
                if cmd.timeout_s:
                    mcp_raw["timeout_s"] = cmd.timeout_s

            try:
                mcp_spec = McpProviderSpec.model_validate(mcp_raw)
            except Exception as e:
                return "rejected", {"error": f"Invalid MCP spec: {e}"}

            conn_id = cmd.connection_id or f"{provider_id}-01"
            auth_type = mcp_spec.auth
            next_step = "farm mcp sync"

            if auth_type == "oauth":
                auth_ref = cmd.auth_ref or f"token-store:{conn_id}"
                status = "needs_login"
                next_step = f"farm mcp login {conn_id}"
            elif auth_type == "env":
                auth_ref = cmd.auth_ref or f"env:{provider_id.upper()}_KEY"
                env_var = auth_ref.split(":", 1)[1]
                if env_var in os.environ and os.environ[env_var].strip():
                    status = "active"
                    next_step = "farm mcp sync"
                else:
                    status = "needs_login"
                    next_step = f"farm set-secret {env_var}"
            else:
                auth_ref = cmd.auth_ref or "cli:none"
                missing_vars = [
                    v.split(":", 1)[1]
                    for v in mcp_spec.env.values()
                    if v.startswith("env:") and not os.environ.get(v.split(":", 1)[1])
                ]
                if missing_vars:
                    status = "needs_login"
                    next_step = f"farm set-secret {missing_vars[0]}"
                else:
                    status = "active"
                    next_step = "farm mcp sync"

            config = {**cmd.config, "mcp": mcp_spec.model_dump(mode="json")}
            async with pool.connection() as conn:
                await conn.execute(
                    "insert into public.providers "
                    "(id, name, kind, executor, default_strategy, enabled, config) "
                    "values (%s, %s, 'tool', 'mcp', %s, %s, %s)",
                    (
                        provider_id,
                        cmd.name or provider_id,
                        cmd.default_strategy,
                        cmd.enabled,
                        Jsonb(config),
                    ),
                )
                await conn.execute(
                    "insert into public.connections (id, provider_id, label, auth_ref, scope, priority, "
                    "strategy, concurrency, rate_per_min, status, plan, meta) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        conn_id,
                        provider_id,
                        cmd.label or conn_id,
                        auth_ref,
                        ["internal"],
                        cmd.priority,
                        None,
                        cmd.concurrency,
                        cmd.rate_per_min,
                        status,
                        Jsonb(cmd.plan),
                        Jsonb(cmd.meta),
                    ),
                )
                for u_name, u_spec in cmd.units.items():
                    await conn.execute(
                        "insert into public.consumption_units (connection_id, unit, limit_value, period, "
                        "reset_anchor, charged_on, unit_cost_usd, estimate_per_call) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            conn_id,
                            u_name,
                            u_spec.limit,
                            u_spec.period,
                            u_spec.anchor,
                            u_spec.charged_on,
                            u_spec.unit_cost_usd,
                            u_spec.estimate_per_call,
                        ),
                    )
                # Ensure capability mcp:<provider_id>
                mcp_cap = f"mcp:{provider_id}"
                await conn.execute(
                    "insert into public.capabilities (name, kind, description) values (%s, 'tool', %s) "
                    "on conflict (name) do nothing",
                    (mcp_cap, f"Pass-through MCP tools of {provider_id}"),
                )
                await conn.execute(
                    "insert into public.capability_routes (capability, provider_id, position, enabled) "
                    "values (%s, %s, 0, true) on conflict (capability, provider_id) do nothing",
                    (mcp_cap, provider_id),
                )
                await _emit_audit(
                    conn,
                    actor,
                    "add_provider",
                    provider_id,
                    None,
                    {"connection_id": conn_id, "kind": "mcp", "status": status},
                )
                await conn.commit()

            await write_registry_file(pool, registry_path)

            # Sync tools via OPEN1 sync
            tools_count = 0
            sync_error = None
            if status == "active":
                from farm.executors.mcp.client import McpExecutor
                from farm.mcp import store as mcp_store
                from farm.mcp.sync import sync_provider

                provider_entry = mcp_store.McpProvider(
                    id=provider_id,
                    name=cmd.name or provider_id,
                    enabled=cmd.enabled,
                    spec=mcp_spec,
                )
                executor = McpExecutor(directory=mcp_store.DbDirectory(pool))
                try:
                    sync_res = await sync_provider(pool, executor, provider_entry)
                    tools_count = len(sync_res.tools)
                    if not sync_res.ok:
                        sync_error = sync_res.error
                finally:
                    await executor.aclose()

                await refresh_active_gateways()

            return "done", {
                "provider_id": provider_id,
                "connection_id": conn_id,
                "status": status,
                "next_step": next_step,
                "tools_count": tools_count,
                "restart_required": False,
                "sync_error": sync_error,
            }

        elif is_openapi:
            if existing_provider is not None:
                return "rejected", {"error": f"Provider '{provider_id}' already exists"}

            conn_id = cmd.connection_id or f"{provider_id}-01"
            auth_env = cmd.auth_env
            if auth_env:
                auth_ref = f"env:{auth_env}"
                if auth_env in os.environ and os.environ[auth_env].strip():
                    status = "active"
                    next_step = "Connection ready"
                else:
                    status = "needs_login"
                    next_step = f"farm set-secret {auth_env}"
            else:
                auth_ref = cmd.auth_ref or "cli:none"
                status = "active"
                next_step = "Connection ready"

            config = {**cmd.config, "spec": cmd.spec}
            async with pool.connection() as conn:
                await conn.execute(
                    "insert into public.providers "
                    "(id, name, kind, executor, default_strategy, enabled, config) "
                    "values (%s, %s, 'tool', 'api', %s, %s, %s)",
                    (
                        provider_id,
                        cmd.name or provider_id,
                        cmd.default_strategy,
                        cmd.enabled,
                        Jsonb(config),
                    ),
                )
                await conn.execute(
                    "insert into public.connections (id, provider_id, label, auth_ref, scope, priority, "
                    "strategy, concurrency, rate_per_min, status, plan, meta) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        conn_id,
                        provider_id,
                        cmd.label or conn_id,
                        auth_ref,
                        ["internal"],
                        cmd.priority,
                        None,
                        cmd.concurrency,
                        cmd.rate_per_min,
                        status,
                        Jsonb(cmd.plan),
                        Jsonb(cmd.meta),
                    ),
                )
                await _emit_audit(
                    conn,
                    actor,
                    "add_provider",
                    provider_id,
                    None,
                    {"connection_id": conn_id, "kind": "openapi", "status": status},
                )
                await conn.commit()

            await write_registry_file(pool, registry_path)
            return "done", {
                "provider_id": provider_id,
                "connection_id": conn_id,
                "status": status,
                "next_step": next_step,
            }

        return "rejected", {"error": "Unrecognized provider configuration"}

    elif kind == "update_provider":
        if isinstance(payload, dict):
            try:
                cmd_up = UpdateProviderPayload.model_validate(payload)
            except ValidationError as err:
                return "rejected", {"error": f"Invalid update_provider payload: {err}"}
        else:
            assert isinstance(payload, UpdateProviderPayload)
            cmd_up = payload

        async with pool.connection() as conn:
            cur = await conn.execute(
                "select id, name, enabled, default_strategy, config from public.providers where id = %s",
                (cmd_up.provider_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return "rejected", {"error": f"Provider '{cmd_up.provider_id}' not found"}

            before = {
                "name": row[1],
                "enabled": row[2],
                "default_strategy": row[3],
                "config": row[4],
            }

            updates: dict[str, Any] = {}
            if cmd_up.name is not None:
                updates["name"] = cmd_up.name
            if cmd_up.enabled is not None:
                updates["enabled"] = cmd_up.enabled
            if cmd_up.default_strategy is not None:
                updates["default_strategy"] = cmd_up.default_strategy

            config = dict(row[4] or {})
            if cmd_up.config is not None:
                config.update(cmd_up.config)
                updates["config"] = Jsonb(config)
            if cmd_up.mcp is not None:
                config["mcp"] = cmd_up.mcp
                updates["config"] = Jsonb(config)

            if updates:
                set_clauses = [f"{k} = %s" for k in updates]
                values = list(updates.values()) + [cmd_up.provider_id]
                await conn.execute(
                    f"update public.providers set {', '.join(set_clauses)} where id = %s",
                    values,
                )

            after = {**before, **{k: getattr(cmd_up, k) for k in updates if hasattr(cmd_up, k)}}
            await _emit_audit(conn, actor, "update_provider", cmd_up.provider_id, before, after)
            await conn.commit()

        await write_registry_file(pool, registry_path)
        return "done", {"provider_id": cmd_up.provider_id, "updated": list(updates.keys())}

    elif kind == "remove_provider":
        if isinstance(payload, dict):
            try:
                cmd_rm = RemoveProviderPayload.model_validate(payload)
            except ValidationError as err:
                return "rejected", {"error": f"Invalid remove_provider payload: {err}"}
        else:
            assert isinstance(payload, RemoveProviderPayload)
            cmd_rm = payload

        async with pool.connection() as conn:
            cur = await conn.execute(
                "select id from public.providers where id = %s",
                (cmd_rm.provider_id,),
            )
            if await cur.fetchone() is None:
                return "rejected", {"error": f"Provider '{cmd_rm.provider_id}' not found"}

            if not cmd_rm.force:
                cur_res = await conn.execute(
                    "select count(*) from public.quota_reservations qr "
                    "join public.connections c on qr.connection_id = c.id "
                    "where c.provider_id = %s and qr.status = 'reserved'",
                    (cmd_rm.provider_id,),
                )
                res_row = await cur_res.fetchone()
                res_count = int(res_row[0]) if res_row is not None else 0

                cur_jobs = await conn.execute(
                    "select count(*) from public.ai_jobs aj "
                    "where (aj.ai = %s or aj.account in ("
                    "  select id from public.connections where provider_id = %s"
                    ")) and aj.state in ('queued', 'running')",
                    (cmd_rm.provider_id, cmd_rm.provider_id),
                )
                jobs_row = await cur_jobs.fetchone()
                jobs_count = int(jobs_row[0]) if jobs_row is not None else 0

                if res_count > 0 or jobs_count > 0:
                    return "rejected", {
                        "error": (
                            f"Provider '{cmd_rm.provider_id}' has {res_count} active reservation(s) "
                            f"and {jobs_count} running job(s); use force=True to remove anyway"
                        )
                    }

            await conn.execute(
                "delete from public.capability_routes where provider_id = %s",
                (cmd_rm.provider_id,),
            )
            await conn.execute(
                "delete from public.capabilities where name = %s",
                (f"mcp:{cmd_rm.provider_id}",),
            )
            await conn.execute(
                "delete from public.mcp_tools where provider = %s",
                (cmd_rm.provider_id,),
            )
            await conn.execute(
                "delete from public.connections where provider_id = %s",
                (cmd_rm.provider_id,),
            )
            await conn.execute(
                "delete from public.providers where id = %s",
                (cmd_rm.provider_id,),
            )
            await _emit_audit(conn, actor, "remove_provider", cmd_rm.provider_id, None, {"status": "deleted"})
            await conn.commit()

        await write_registry_file(pool, registry_path)
        return "done", {"provider_id": cmd_rm.provider_id, "status": "deleted"}

    return "rejected", {"error": f"Unhandled provider command kind '{kind}'"}


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
                    await conn.execute(
                        "insert into public.consumption_units "
                        "(connection_id, unit, limit_value, period, reset_anchor, "
                        "charged_on, unit_cost_usd, estimate_per_call) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            payload.id,
                            unit_name,
                            unit_spec.limit,
                            unit_spec.period,
                            unit_spec.anchor,
                            unit_spec.charged_on,
                            unit_spec.unit_cost_usd,
                            unit_spec.estimate_per_call,
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
                    "select id, provider_id, auth_ref, meta, concurrency, rate_per_min, status "
                    "from public.connections where id = %s",
                    (payload.connection_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}

                conn_id, provider_id, auth_ref, meta, concurrency, rate_per_min, curr_status = row
                meta = meta or {}
                conn_view = ConnectionView(
                    id=conn_id,
                    provider_id=provider_id,
                    auth_ref=auth_ref,
                    meta=meta,
                    concurrency=concurrency or 1,
                    rate_per_min=rate_per_min,
                )

                auth_ok = False
                auth_err = None

                if auth_ref.startswith("env:"):
                    try:
                        resolve_auth(conn_view.auth_ref)
                        auth_ok = True
                    except AuthRefError as aerr:
                        auth_ok = False
                        auth_err = str(aerr)
                elif auth_ref == "cli:none":
                    auth_ok = True
                elif auth_ref.startswith("cli:"):
                    if meta.get("logged_in") is True:
                        auth_ok = True
                    else:
                        try:
                            from farm.executors.cli_agent import CliAgentExecutor

                            executor = CliAgentExecutor()
                            req = ExecRequest(
                                request_id=uuid4(),
                                capability=payload.capability or "ask_ai",
                                params={"task": "ping", "prompt": "ping", "mode": "answer"},
                                connection=conn_view,
                                timeout_s=10.0,
                            )
                            res = await executor.execute(req)
                            if res.ok:
                                auth_ok = True
                            elif res.error_kind in (ErrorKind.NEEDS_LOGIN, ErrorKind.AUTH):
                                auth_ok = False
                                auth_err = res.error or "Login required"
                            else:
                                auth_ok = False
                                auth_err = res.error
                        except Exception as e:
                            auth_ok = False
                            auth_err = str(e)
                elif auth_ref.startswith("token-store:"):
                    try:
                        token_dir = resolve_token_store(auth_ref)
                        if any(token_dir.iterdir()):
                            auth_ok = True
                        else:
                            auth_ok = False
                            auth_err = f"token-store '{auth_ref}' is empty; login required"
                    except Exception as e:
                        auth_ok = False
                        auth_err = str(e)
                else:
                    try:
                        resolve_auth(conn_view.auth_ref)
                        auth_ok = True
                    except Exception as aerr:
                        auth_ok = False
                        auth_err = str(aerr)

                if auth_ok and curr_status == "needs_login":
                    await conn.execute(
                        "update public.connections set status = 'active' where id = %s",
                        (payload.connection_id,),
                    )
                    curr_status = "active"
                    from farm.registry.writer import write_registry_file

                    await conn.commit()
                    await write_registry_file(pool)

                result_data = {
                    "connection_id": payload.connection_id,
                    "auth_ok": auth_ok,
                    "auth_error": auth_err,
                    "status": curr_status,
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

            elif kind == "cancel_ai_job":
                assert isinstance(payload, CancelAiJobPayload)
                jid = payload.job_id if isinstance(payload.job_id, UUID) else UUID(str(payload.job_id))
                cur = await conn.execute(
                    "select id, state, owner_id from public.ai_jobs where id = %s",
                    (jid,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"AI job '{jid}' not found"}

                curr_state, owner_id = row[1], row[2]
                if curr_state in ("succeeded", "failed", "cancelled"):
                    return "done", {
                        "job_id": str(jid),
                        "state": curr_state,
                        "cancelled": curr_state == "cancelled",
                    }

                await conn.execute(
                    "update public.ai_jobs set cancel_requested_at = coalesce(cancel_requested_at, now()) "
                    "where id = %s and state in ('queued', 'running')",
                    (jid,),
                )
                if curr_state == "queued" and owner_id is None:
                    await conn.execute(
                        "update public.ai_jobs set state = 'cancelled', error_kind = 'cancelled', "
                        "error_message = 'cancelled by caller', finished_at = now() "
                        "where id = %s and state = 'queued'",
                        (jid,),
                    )
                    curr_state = "cancelled"

                await _emit_audit(
                    conn,
                    actor,
                    "cancel_ai_job",
                    str(jid),
                    {"state": row[1]},
                    {"state": curr_state, "cancel_requested": True},
                )
                return "done", {
                    "job_id": str(jid),
                    "state": curr_state,
                    "cancelled": curr_state == "cancelled",
                }

            elif kind == "set_max_parallel":
                assert isinstance(payload, SetMaxParallelPayload)
                target = payload.connection_id or payload.provider_id or ""
                before = {}
                after = {"max_parallel": payload.max_parallel}

                if payload.connection_id:
                    cur = await conn.execute(
                        "select concurrency, meta from public.connections where id = %s",
                        (payload.connection_id,),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return "rejected", {"error": f"Connection '{payload.connection_id}' not found"}
                    curr_concurrency = row[0]
                    curr_meta = row[1] or {}
                    before["concurrency"] = curr_concurrency
                    before["max_parallel"] = curr_meta.get("max_parallel")

                    new_concurrency = max(curr_concurrency, payload.max_parallel)
                    new_meta = {**curr_meta, "max_parallel": payload.max_parallel}
                    await conn.execute(
                        "update public.connections set concurrency = %s, meta = %s where id = %s",
                        (new_concurrency, Jsonb(new_meta), payload.connection_id),
                    )
                    after["concurrency"] = new_concurrency

                if payload.provider_id:
                    cur = await conn.execute(
                        "select config from public.providers where id = %s",
                        (payload.provider_id,),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return "rejected", {"error": f"Provider '{payload.provider_id}' not found"}
                    curr_config = row[0] or {}
                    before["provider_max_parallel"] = curr_config.get("max_parallel")
                    new_config = {**curr_config, "max_parallel": payload.max_parallel}
                    await conn.execute(
                        "update public.providers set config = %s where id = %s",
                        (Jsonb(new_config), payload.provider_id),
                    )

                await _emit_audit(conn, actor, "set_max_parallel", target, before, after)
                return "done", {"target": target, "max_parallel": payload.max_parallel}

            elif kind == "set_mcp_tool_access":
                assert isinstance(payload, SetMcpToolAccessPayload)
                cur = await conn.execute(
                    "select config from public.providers where id = %s",
                    (payload.provider_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return "rejected", {"error": f"Provider '{payload.provider_id}' not found"}

                config = row[0] or {}
                mcp_block = config.get("mcp") or {}
                tools_spec = mcp_block.get("tools") or {}
                allow_list = list(tools_spec.get("allow") or ["*"])
                deny_list = list(tools_spec.get("deny") or [])

                before = {
                    "allow": list(allow_list),
                    "deny": list(deny_list),
                }

                if payload.enabled:
                    deny_list = [d for d in deny_list if d != payload.tool]
                    if "*" not in allow_list and payload.tool not in allow_list:
                        allow_list.append(payload.tool)
                else:
                    if payload.tool not in deny_list:
                        deny_list.append(payload.tool)
                    if "*" not in allow_list:
                        allow_list = [a for a in allow_list if a != payload.tool]

                tools_spec["allow"] = allow_list
                tools_spec["deny"] = deny_list
                mcp_block["tools"] = tools_spec
                config["mcp"] = mcp_block

                await conn.execute(
                    "update public.providers set config = %s where id = %s",
                    (Jsonb(config), payload.provider_id),
                )

                target = f"{payload.provider_id}:{payload.tool}"
                after = {
                    "tool": payload.tool,
                    "enabled": payload.enabled,
                    "allow": allow_list,
                    "deny": deny_list,
                }
                await _emit_audit(conn, actor, "set_mcp_tool_access", target, before, after)
                return "done", {"target": target, "tool": payload.tool, "enabled": payload.enabled}

            elif kind == "sync_mcp_tools":
                assert isinstance(payload, SyncMcpToolsPayload)
                from farm.executors.mcp.client import McpExecutor
                from farm.mcp.store import DbDirectory
                from farm.mcp.sync import sync_all

                target = payload.provider_id or "all"
                mcp_exec = McpExecutor(directory=DbDirectory(pool))
                try:
                    results = await sync_all(pool, mcp_exec, only=payload.provider_id)
                finally:
                    await mcp_exec.aclose()

                res_list = [
                    {
                        "provider": r.provider,
                        "ok": r.ok,
                        "connection": r.connection,
                        "tools_count": len(r.tools),
                        "denied_count": len(r.denied),
                        "error": r.error,
                    }
                    for r in results
                ]
                after = {"results": res_list}
                await _emit_audit(conn, actor, "sync_mcp_tools", target, None, after)
                all_ok = all(r.ok for r in results) if results else True
                return ("done" if all_ok else "failed"), {"target": target, "results": res_list}

            elif kind in ("add_provider", "update_provider", "remove_provider"):
                assert isinstance(payload, (AddProviderPayload, UpdateProviderPayload, RemoveProviderPayload))
                return await execute_provider_command(pool, kind, payload, actor=actor)

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
