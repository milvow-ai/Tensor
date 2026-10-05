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
import secrets
import signal
import time
from contextvars import ContextVar
from typing import Any

import mcp_types as mt
import structlog
import uvicorn
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext
from fastmcp.tools import ToolResult
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from farm.ai.jobs import JobManager
from farm.context import FarmContext, build_context
from farm.control.commands import CommandConsumer
from farm.control.heartbeat import HeartbeatWriter
from farm.control.keepawake import disable_keepawake, enable_keepawake
from farm.db.pool import DbPool, get_db_url
from farm.executors.mcp.client import McpExecutor
from farm.gateway.ai_tools import register_ai_tools
from farm.gateway.middleware import (
    AuthMiddleware,
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

log = structlog.get_logger(__name__)

authenticated_client_name: ContextVar[str | None] = ContextVar("farm_authenticated_client", default=None)


# --- Token Management in Database -------------------------------------------------------------------------


async def ensure_tokens_table(pool: DbPool) -> None:
    """Ensure public.client_tokens exists in the database."""
    async with pool.connection() as conn:
        await conn.execute(
            """
            create table if not exists public.client_tokens (
                token_hash text primary key,
                client_name text not null,
                created_at timestamptz not null default now(),
                last_used timestamptz
            );
            alter table public.client_tokens enable row level security;
            """
        )


def hash_token(raw_token: str) -> str:
    """Compute the SHA-256 hash of a raw token."""
    return hashlib.sha256(raw_token.strip().encode()).hexdigest()


async def create_token(pool: DbPool, client_name: str) -> str:
    """Generate a new random token, store its hash in the DB, and return the raw token.

    The raw token is returned once and must never be stored or logged.
    """
    await ensure_tokens_table(pool)
    raw_token = f"farm_{secrets.token_urlsafe(32)}"
    tok_hash = hash_token(raw_token)

    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into public.client_tokens (token_hash, client_name, created_at)
            values (%s, %s, now())
            """,
            (tok_hash, client_name.strip()),
        )
    log.info("token.created", client=client_name.strip())
    return raw_token


async def list_tokens(pool: DbPool) -> list[dict[str, Any]]:
    """List all registered client tokens without exposing secrets."""
    await ensure_tokens_table(pool)
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            select client_name, token_hash, created_at, last_used
            from public.client_tokens
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
            }
            for row in rows
        ]


async def revoke_token(pool: DbPool, identifier: str) -> bool:
    """Revoke a token by client name or token hash."""
    await ensure_tokens_table(pool)
    ident = identifier.strip()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            delete from public.client_tokens
            where client_name = %s or token_hash = %s
            """,
            (ident, ident),
        )
        revoked = cur.rowcount > 0
    if revoked:
        log.info("token.revoked", identifier=ident)
    return revoked


async def verify_token(pool: DbPool, raw_token: str) -> str | None:
    """Validate raw_token using constant-time hash comparison and return client_name."""
    await ensure_tokens_table(pool)
    tok = raw_token.strip()
    if not tok:
        return None
    incoming_hash = hash_token(tok)

    async with pool.connection() as conn:
        cur = await conn.execute("select token_hash, client_name from public.client_tokens")
        rows = await cur.fetchall()

        matched_client: str | None = None
        matched_hash: str | None = None
        for row_hash, client_name in rows:
            if hmac.compare_digest(row_hash.encode(), incoming_hash.encode()):
                matched_client = str(client_name)
                matched_hash = str(row_hash)
                break

        if matched_client is not None and matched_hash is not None:
            await conn.execute(
                "update public.client_tokens set last_used = now() where token_hash = %s",
                (matched_hash,),
            )
            return matched_client

    return None


# --- Custom Middlewares -----------------------------------------------------------------------------------


class TokenTrajectoryMiddleware(Middleware):
    """Trajectory middleware mapping the authenticated token to current_caller for run rows."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        client = authenticated_client_name.get()
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
        client_name: str | None = None
        if scheme.lower() == "bearer" and token_val:
            client_name = await verify_token(self.pool, token_val.strip())

        if client_name is None:
            log.warning("gateway.auth_refused", path=path)
            unauth_resp = Response(
                content="Unauthorized: valid bearer token required\n",
                status_code=401,
                media_type="text/plain",
                headers={"WWW-Authenticate": "Bearer"},
            )
            await unauth_resp(scope, receive, send)
            return

        tok = authenticated_client_name.set(client_name)
        try:
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
        results = await sync_all(ctx.pool, executor)
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
    server.middleware.append(TokenTrajectoryMiddleware())

    # Wire JobManager to AI tools if provided
    if job_manager is not None:
        await register_ai_tools(server, ctx, routing_argument="routing_strategy", manager=job_manager)

    # Build the FastMCP streamable-http app
    base_app = server.http_app(transport="streamable-http", path=endpoint_path)

    # Wrap with FarmAuthMiddleware
    return FarmAuthMiddleware(base_app, ctx.pool, mcp_path=endpoint_path)


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

    await ensure_tokens_table(ctx.pool)
    enable_keepawake(away_mode=True, data_directory=data_dir())

    job_manager = JobManager(ctx)
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
        await command_consumer.stop()
        await scheduler.stop()
        await heartbeat_writer.stop()

        disable_keepawake(data_directory=data_dir())
        await ctx.aclose()
        log.info("farm_run.stopped")
