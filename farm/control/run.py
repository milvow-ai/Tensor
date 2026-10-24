"""Composed 24/7 Farm runner: FastMCP streamable HTTP gateway with token auth and background workers.

Process composition (brief RUN1):
- FastMCP Streamable HTTP server on settings.http_host:settings.http_port at /mcp
- /health endpoint for watchdog health checks
- Per-client token authentication (Authorization: Bearer <token>)
- Client name mapping to runs.caller
- CommandConsumer (farm_commands polling every 2s)
- JobManager (AI job runner) with graceful drain and AIP2 restart rules
- HeartbeatWriter (command_consumer heartbeat every 15s)
- ManagerScheduler (maintenance tasks: expire, reactivate, budgets, renewals)
- MCP tool catalog refresh in background
- Windows keep-awake active during execution
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import signal
import time
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID

import mcp_types as mt
import structlog
import uvicorn
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext
from fastmcp.tools import ToolResult
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from farm.ai.jobs import JobManager
from farm.connect.drivers.registry import driver_for
from farm.connect.service import CheckResult, LoginService, set_login_service
from farm.context import FarmContext, build_context
from farm.control.commands import CommandConsumer
from farm.control.heartbeat import HeartbeatWriter
from farm.control.keepawake import disable_keepawake, enable_keepawake
from farm.db.pool import DbPool, get_db_url, system_connection, tenant_connection
from farm.executors.mcp.client import McpExecutor
from farm.gateway.ai_tools import register_ai_tools
from farm.gateway.middleware import (
    IDENTITY_SCOPE_KEY,
    AuthMiddleware,
    TenantMiddleware,
    TrajectoryMiddleware,
    _client_name,
    caller_from_client_name,
    current_caller,
)
from farm.gateway.server import build_server
from farm.manager.scheduler import ManagerScheduler
from farm.mcp.store import DbDirectory
from farm.mcp.sync import sync_all
from farm.settings import data_dir, http_host, http_path, http_port
from farm.tenancy import MILVOW_TENANT_ID, TokenIdentity, current_tenant, use_tenant
from farm.tenancy.active import for_each_tenant

log = structlog.get_logger(__name__)


async def _ensure_owner_member(pool: DbPool) -> None:
    """The Console owner needs a membership to pass RLS; a failure is logged, never stops the Farm."""
    import httpx

    from farm.tenancy.members import MemberError, ensure_owner_member

    supabase_url, service_key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        log.info("owner_member.skipped", reason="SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        return
    try:
        added = await ensure_owner_member(pool, supabase_url=supabase_url, service_key=service_key)
    except (MemberError, httpx.HTTPError) as exc:
        log.warning("owner_member.failed", error=type(exc).__name__, detail=str(exc)[:200])
        return
    if added:
        log.info("owner_member.added", email=added)

authenticated_client_name: ContextVar[str | None] = ContextVar("farm_authenticated_client", default=None)


# --- Token Management in Database -------------------------------------------------------------------------

TOKEN_PREFIX = "hf_"
PREFIX_CHARS = 8
"""Leading characters of a token kept in clear (``api_tokens.token_prefix``) to tell tokens apart."""


def hash_token(raw_token: str) -> str:
    """Compute the SHA-256 hash of a raw token."""
    return hashlib.sha256(raw_token.strip().encode()).hexdigest()


async def create_token(
    pool: DbPool,
    client_name: str,
    *,
    user_id: UUID | None = None,
    scopes: Sequence[str] = ("*",),
) -> str:
    """Generate a new random token for the current tenant, store its hash, and return the raw token.

    The raw token is returned once and must never be stored or logged.
    """
    raw_token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    async with tenant_connection(pool) as conn:
        await conn.execute(
            """
            insert into public.api_tokens (user_id, name, token_prefix, token_hash, scopes)
            values (%s, %s, %s, %s, %s)
            """,
            (user_id, client_name.strip(), raw_token[:PREFIX_CHARS], hash_token(raw_token), list(scopes)),
        )
    log.info("token.created", client=client_name.strip())
    return raw_token


async def list_tokens(pool: DbPool) -> list[dict[str, Any]]:
    """List the current tenant's live API tokens without exposing secrets."""
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            """
            select name, token_hash, created_at, last_used_at, token_prefix, scopes
            from public.api_tokens
            where revoked_at is null
            order by created_at desc
            """
        )
        rows = await cur.fetchall()
    return [
        {
            "client_name": row[0],
            "token_hash": row[1],
            "created_at": row[2],
            "last_used": row[3],
            "token_prefix": row[4],
            "scopes": list(row[5]),
        }
        for row in rows
    ]


async def revoke_token(pool: DbPool, identifier: str) -> bool:
    """Revoke the current tenant's tokens by client name or token hash."""
    ident = identifier.strip()
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            """
            update public.api_tokens set revoked_at = now()
            where revoked_at is null and (name = %s or token_hash = %s)
            """,
            (ident, ident),
        )
        revoked = cur.rowcount > 0
    if revoked:
        log.info("token.revoked", identifier=ident)
    return revoked


async def verify_token(pool: DbPool, raw_token: str) -> TokenIdentity | None:
    """Resolve a raw token to the tenant, user and scopes it acts for (None: unknown, revoked, suspended).

    This runs before any tenant is known, so it is one of the few cross-tenant reads (``system_connection``).
    The stored hash is compared in constant time.
    """
    tok = raw_token.strip()
    if not tok:
        return None
    incoming_hash = hash_token(tok)

    async with system_connection(pool) as conn:
        cur = await conn.execute(
            """
            select a.id, a.tenant_id, a.user_id, a.name, a.scopes, a.token_hash
            from public.api_tokens a join public.tenants t on t.id = a.tenant_id
            where a.token_hash = %s and a.revoked_at is null and t.status = 'active'
            """,
            (incoming_hash,),
        )
        row = await cur.fetchone()
        if row is None or not hmac.compare_digest(str(row[5]).encode(), incoming_hash.encode()):
            return None
        await conn.execute("update public.api_tokens set last_used_at = now() where id = %s", (row[0],))
    return TokenIdentity(
        token_id=row[0], tenant_id=row[1], user_id=row[2], name=str(row[3]), scopes=tuple(row[4])
    )


# --- Custom Middlewares -----------------------------------------------------------------------------------


def _request_identity_name() -> str | None:
    """The client name of the token behind the MCP request being handled (per request, not per session)."""
    try:
        identity = get_http_request().scope.get(IDENTITY_SCOPE_KEY)
    except RuntimeError:
        return None
    return identity.name if isinstance(identity, TokenIdentity) else None


class TokenTrajectoryMiddleware(Middleware):
    """Trajectory middleware mapping the authenticated token to current_caller for run rows."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        client = _request_identity_name() or authenticated_client_name.get()
        caller = client or caller_from_client_name(_client_name(context))
        tok = current_caller.set(caller)
        started = time.monotonic()
        outcome = "error"
        try:
            result = await call_next(context)
            outcome = "error" if result.is_error else "ok"
            return result
        finally:
            current_caller.reset(tok)
            log.info(
                "gateway.tool_call",
                tool=context.message.name,
                caller=caller,
                outcome=outcome,
                ms=int((time.monotonic() - started) * 1000),
            )


class FarmAuthMiddleware:
    """ASGI middleware handling /health and Bearer token auth for FastMCP endpoints."""

    def __init__(self, app: ASGIApp, pool: DbPool, mcp_path: str = "/mcp") -> None:
        self.app = app
        self.pool = pool
        self.mcp_path = mcp_path.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        # 1. Health endpoint for watchdog and doctor
        if path == "/health" or path == "/health/":
            health_resp = JSONResponse({"status": "ok", "service": "farm-run"})
            await health_resp(scope, receive, send)
            return

        # 2. Authenticate bearer token for MCP endpoints
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("latin1").strip()
        scheme, _, token_val = auth_header.partition(" ")
        identity: TokenIdentity | None = None
        if scheme.lower() == "bearer" and token_val:
            identity = await verify_token(self.pool, token_val.strip())

        if identity is None:
            log.warning("gateway.auth_refused", path=path)
            unauth_resp = Response(
                content="Unauthorized: valid bearer token required\n",
                status_code=401,
                media_type="text/plain",
                headers={"WWW-Authenticate": "Bearer"},
            )
            await unauth_resp(scope, receive, send)
            return

        scope[IDENTITY_SCOPE_KEY] = identity  # read per MCP request by TenantMiddleware
        tok = authenticated_client_name.set(identity.name)
        try:
            with use_tenant(identity.tenant_id, identity.user_id):
                await self.app(scope, receive, send)
        finally:
            authenticated_client_name.reset(tok)


# --- MCP Tool Sync Helper ---------------------------------------------------------------------------------


async def refresh_mcp_catalogue(ctx: FarmContext) -> None:
    """Refresh connected MCP servers' tool catalogs in background."""
    executor = ctx.executors.get("mcp")
    if not isinstance(executor, McpExecutor):
        return
    try:
        # The catalogue is per tenant: refresh it for each active tenant.
        refreshed = await for_each_tenant(ctx.pool, lambda: sync_all(ctx.pool, executor), task="mcp_refresh")
        for results in refreshed:
            log.info(
                "mcp.refreshed",
                providers=len(results),
                failed=[r.provider for r in results if not r.ok],
            )
    except Exception as exc:
        log.error("mcp.refresh_failed", error=type(exc).__name__)


# --- Process Composition & Run ----------------------------------------------------------------------------


async def create_farm_app(
    ctx: FarmContext,
    *,
    job_manager: JobManager | None = None,
    path: str | None = None,
) -> ASGIApp:
    """Create the configured FastMCP Streamable HTTP app wrapped with Farm auth."""
    from farm.control.cli import _mcp_exposure

    endpoint_path = path or http_path()
    limit, pinned = _mcp_exposure()

    server = await build_server(ctx, mcp_direct_limit=limit, mcp_pinned=pinned)

    # Replace AuthMiddleware and TrajectoryMiddleware with token-aware middlewares
    server.middleware = [
        m for m in server.middleware if not isinstance(m, (AuthMiddleware, TrajectoryMiddleware))
    ]
    server.middleware.insert(0, TenantMiddleware())
    server.middleware.append(TokenTrajectoryMiddleware())

    # Wire JobManager to AI tools if provided
    if job_manager is not None:
        await register_ai_tools(server, ctx, routing_argument="routing_strategy", manager=job_manager)

    # Build the FastMCP streamable-http app
    base_app = server.http_app(transport="streamable-http", path=endpoint_path)

    # Wrap with FarmAuthMiddleware
    return FarmAuthMiddleware(base_app, ctx.pool, mcp_path=endpoint_path)



async def _no_login_test(connection_id: str, home: Path) -> CheckResult:
    # Every driver tests its own login (a one-shot prompt); a driver without one cannot finish a sign-in.
    raise NotImplementedError("this sign-in driver has no test of its own")


async def run_farm(
    host: str | None = None,
    port: int | None = None,
    local: bool = False,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Run the 24/7 Harness Farm process composition.

    Serves FastMCP Streamable HTTP on host:port, runs command consumer,
    AI job runner, heartbeat writer, and manager scheduler.
    """
    target_host = host or http_host()
    target_port = port or http_port()
    target_path = http_path()

    ctx = await build_context(get_db_url(force_local=True) if local else None)
    ctx.executors.setdefault("mcp", McpExecutor(directory=DbDirectory(ctx.pool)))

    # The HTTP process has no ambient tenant: each request, job, command and maintenance pass sets its own.
    current_tenant.set(None)
    enable_keepawake(away_mode=True, data_directory=data_dir())

    job_manager = JobManager(ctx)
    # One tool surface per process until the per-tenant catalogue (A1, D51): it is built from tenant #1's MCP
    # providers. Calls through it still run for the caller's tenant (TenantMiddleware sets it per request).
    with use_tenant(MILVOW_TENANT_ID):
        app = await create_farm_app(ctx, job_manager=job_manager, path=target_path)

    heartbeat_writer = HeartbeatWriter(
        component="command_consumer",
        interval_s=15.0,
        data_directory=data_dir(),
        pool=ctx.pool,
    )
    command_consumer = CommandConsumer(ctx.pool, poll_interval_s=2.0)
    scheduler = ManagerScheduler(ctx.pool)

    # Start background components
    await heartbeat_writer.start()
    await command_consumer.start()
    login_service = LoginService(ctx.pool, driver_factory=driver_for, test_job=_no_login_test)
    set_login_service(login_service)
    await login_service.start()
    await _ensure_owner_member(ctx.pool)
    await scheduler.start()

    # Trigger background MCP catalogue refresh
    mcp_sync_task = asyncio.create_task(refresh_mcp_catalogue(ctx))

    config = uvicorn.Config(
        app,
        host=target_host,
        port=target_port,
        log_level="info",
        lifespan="on",
        access_log=False,
    )
    server = uvicorn.Server(config)

    stop_event = shutdown_event or asyncio.Event()

    def _on_signal(*_: Any) -> None:
        log.info("farm_run.signal_received")
        stop_event.set()
        server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            # Windows may not support add_signal_handler on selector loop
            pass

    async def _monitor_shutdown() -> None:
        await stop_event.wait()
        server.should_exit = True

    shutdown_task = asyncio.create_task(_monitor_shutdown())

    log.info(
        "farm_run.started",
        host=target_host,
        port=target_port,
        path=target_path,
        url=f"http://{target_host}:{target_port}{target_path}",
    )

    try:
        await server.serve()
    finally:
        log.info("farm_run.shutting_down")
        stop_event.set()
        shutdown_task.cancel()
        if not mcp_sync_task.done():
            mcp_sync_task.cancel()

        # Graceful shutdown: let in-flight calls finish up to 30 s
        try:
            await asyncio.wait_for(job_manager.drain(), timeout=30.0)
        except TimeoutError:
            log.warning("farm_run.drain_timeout")

        # Mark remaining AI jobs per AIP2 rules
        await job_manager.shutdown()

        # Stop background workers
        await login_service.stop()
        set_login_service(None)
        await command_consumer.stop()
        await scheduler.stop()
        await heartbeat_writer.stop()

        disable_keepawake(data_directory=data_dir())
        await ctx.aclose()
        log.info("farm_run.stopped")
