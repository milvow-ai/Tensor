"""Gateway middleware: auth -> policy -> trajectory, in that order (``server.build_server`` installs them).

HANDOFF 4.1 names the chain ``auth -> policy -> cache -> single-flight -> trajectory``. Cache and
single-flight are not middleware here: they need the capability's TTL, the request hash and the run, all of
which the router owns; the router is also what the CLI and batch workflows call, so they are served from
one place (``farm.resources.router``). Policy that depends on the request (budget hard stop, scope) lives
there too; the policy hook below is the gate in front of it for *who may call what*.

* ``AuthMiddleware``: stdio is local trust (the client is a child process of whoever started the Farm). Over
  HTTP every request must carry ``Authorization: Bearer <token>`` equal to the Farm's token (constant-time
  comparison); with no token configured an HTTP request is refused, never served openly.
  A static token may be bound to a tenant (``tenant_id``); without one the work runs for the tenant the
  process already carries (stdio: tenant #1 set by the CLI).
* ``TenantMiddleware``: the Farm's own HTTP runner authenticates API tokens in front of the MCP app
  (``farm.control.run``) and leaves the resolved ``TokenIdentity`` on the request scope. This middleware
  reads it *per MCP request* and sets the tenant for the tool that runs. A ContextVar set in front of the app
  is not enough: a stateful MCP session runs its handlers in a task started by its first request, so a later
  request on the same session would otherwise inherit that first request's tenant. No identity over HTTP =
  refused.
* ``PolicyMiddleware``: asks a ``PolicyCheck`` whether this caller may call this tool; the default allows
  every farm tool. A refusal is an MCP error (``policy: <reason>``) and the tool never runs.
* ``TrajectoryMiddleware``: identifies the caller from the client's declared name (``claude-code`` ->
  ``claude``, ``hermes`` -> ``hermes``, anything else ``mcp:<name>``) and exposes it to the tools through
  ``current_caller``, so the run row says who asked. It also logs one structured line per tool call.
"""

from __future__ import annotations

import hmac
import json
import re
import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any
from uuid import UUID

import mcp_types as mt
import structlog
from fastmcp.exceptions import McpError, ToolError
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext
from fastmcp.tools import Tool, ToolResult

from farm.ai.worker_bootstrap import INSPECTION_TOOLS
from farm.secrets import redact_worker_tokens
from farm.tenancy import TokenIdentity, use_tenant

log = structlog.get_logger(__name__)

current_caller: ContextVar[str] = ContextVar("farm_caller", default="mcp")
"""Who is calling, for the tool that is running (set by ``TrajectoryMiddleware``)."""
current_client: ContextVar[str] = ContextVar("farm_client", default="mcp")
"""Normalized MCP client name, independent of an HTTP token's operator label."""
current_identity: ContextVar[TokenIdentity | None] = ContextVar("farm_identity", default=None)
"""Authenticated API token identity for this tool call; None denotes trusted local stdio."""

type PolicyCheck = Callable[[str, str, dict[str, Any]], str | None]
"""``(caller, tool name, arguments) -> reason to refuse, or None to allow``."""

_NAME_UNSAFE = re.compile(r"[^a-z0-9._-]+")
MAX_CALLER_CHARS = 48
UNAUTHORIZED = -32001
"""JSON-RPC server-error code for a refused request (the -32000..-32099 range is implementation-defined)."""


def caller_from_client_name(name: str | None) -> str:
    """Map the client's self-declared name to a ``runs.caller`` value (claude / hermes / mcp:<name>)."""
    cleaned = _NAME_UNSAFE.sub("-", (name or "").strip().lower()).strip("-")
    if "claude" in cleaned:
        return "claude"
    if "hermes" in cleaned:
        return "hermes"
    return f"mcp:{cleaned[:MAX_CALLER_CHARS]}" if cleaned else "mcp"


def _client_name(context: MiddlewareContext[Any]) -> str | None:
    fastmcp_context = context.fastmcp_context
    if fastmcp_context is None:
        return None
    params = fastmcp_context.session.client_params
    return None if params is None else params.client_info.name


IDENTITY_SCOPE_KEY = "farm_identity"
"""ASGI scope key under which the Farm's HTTP runner leaves the request's ``TokenIdentity``."""

WORKER_GATE_SCOPE_KEY = "farm_worker_gate"
_WORKER_MCP_METHODS = frozenset({
    "initialize", "ping", "tools/list", "tools/call", "notifications/initialized",
    "notifications/cancelled", "resources/list", "resources/templates/list", "prompts/list",
})


def request_identity() -> TokenIdentity | None:
    try:
        identity = get_http_request().scope.get(IDENTITY_SCOPE_KEY)
    except RuntimeError:
        return None
    return identity if isinstance(identity, TokenIdentity) else None


class AuthMiddleware(Middleware):
    def __init__(self, token: str | None = None, tenant_id: UUID | None = None) -> None:
        self._token = token
        self._tenant_id = tenant_id

    async def on_request(
        self, context: MiddlewareContext[mt.Request[Any, Any]], call_next: CallNext[mt.Request[Any, Any], Any]
    ) -> Any:
        try:
            get_http_request()
        except RuntimeError:
            return await call_next(context)  # stdio: local trust
        presented = get_http_headers(include={"authorization"}).get("authorization", "")
        scheme, _, value = presented.partition(" ")
        if (
            self._token is None
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(value.strip().encode(), self._token.encode())
        ):
            log.warning("gateway.auth_refused", configured=self._token is not None)
            raise McpError(code=UNAUTHORIZED, message="unauthorized: a valid bearer token is required")
        if self._tenant_id is None:
            return await call_next(context)
        with use_tenant(self._tenant_id):
            return await call_next(context)


class TenantMiddleware(Middleware):
    """Sets the tenant of every MCP request from the identity the HTTP runner resolved (module docstring)."""

    async def on_request(
        self, context: MiddlewareContext[mt.Request[Any, Any]], call_next: CallNext[mt.Request[Any, Any], Any]
    ) -> Any:
        try:
            request = get_http_request()
        except RuntimeError:
            return await call_next(context)  # stdio: the process carries its tenant
        identity = request.scope.get(IDENTITY_SCOPE_KEY)
        if not isinstance(identity, TokenIdentity):
            log.warning("gateway.tenant_unresolved")
            raise McpError(code=UNAUTHORIZED, message="unauthorized: the request has no tenant")
        with use_tenant(identity.tenant_id, identity.user_id):
            return await call_next(context)


class PolicyMiddleware(Middleware):
    def __init__(self, check: PolicyCheck | None = None) -> None:
        self._check = check

    async def on_message(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        identity = request_identity()
        if identity is not None and identity.worker_job_id is not None:
            if context.method not in _WORKER_MCP_METHODS:
                raise McpError(code=UNAUTHORIZED, message="policy: worker credentials are limited to tools")
        return await call_next(context)

    async def on_list_tools(
        self, context: MiddlewareContext[mt.ListToolsRequest],
        call_next: CallNext[mt.ListToolsRequest, Sequence[Tool]],
    ) -> Sequence[Tool]:
        identity = request_identity()
        tools = await call_next(context)
        if identity is None or identity.worker_job_id is None:
            return tools
        from farm.gateway.mcp_tools import PassthroughTool

        return [
            tool for tool in tools if tool_scope_allows(identity.scopes, tool.name)
            and (tool.name in INSPECTION_TOOLS or isinstance(tool, PassthroughTool))
        ]

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        identity = request_identity()
        name = context.message.name
        if identity is not None and not tool_scope_allows(identity.scopes, name):
            raise ToolError("policy: token scope does not allow this tool")
        worker = identity is not None and identity.worker_job_id is not None
        if worker:
            arguments = json.dumps(context.message.arguments)
            if redact_worker_tokens(arguments) != arguments:
                raise ToolError("policy: worker credentials cannot be used as tool arguments")
            if name not in INSPECTION_TOOLS:
                from farm.gateway.mcp_tools import PassthroughTool

                mcp = context.fastmcp_context
                if mcp is None or not isinstance(await mcp.fastmcp.get_tool(name), PassthroughTool):
                    raise ToolError("policy: worker credentials cannot invoke this tool")
        if self._check is not None:
            caller = caller_from_client_name(_client_name(context))
            reason = self._check(caller, context.message.name, dict(context.message.arguments or {}))
            if reason is not None:
                log.warning("gateway.policy_refused", caller=caller, tool=context.message.name, reason=reason)
                raise ToolError(f"policy: {reason}")
        result = await call_next(context)
        if worker:
            payload = result.model_dump_json()
            if redact_worker_tokens(payload) != payload:
                raise ToolError("policy: tool returned a worker credential")
        return result


def tool_scope_allows(scopes: tuple[str, ...], tool: str) -> bool:
    """mcp grants invocation, not recovery approval; exact tool grants never expand to other tools."""
    return (
        "*" in scopes or "mcp" in scopes or f"tool:{tool}" in scopes
        or (tool == "ai_recover" and "ai:recovery:approve" in scopes)
    )


class TrajectoryMiddleware(Middleware):
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        caller = caller_from_client_name(_client_name(context))
        token = current_caller.set(caller)
        client_token = current_client.set(caller)
        try:
            identity = get_http_request().scope.get(IDENTITY_SCOPE_KEY)
        except RuntimeError:
            identity = None
        identity_token = current_identity.set(identity if isinstance(identity, TokenIdentity) else None)
        started = time.monotonic()
        outcome = "error"
        try:
            result = await call_next(context)
            outcome = "error" if result.is_error else "ok"
            return result
        finally:
            current_caller.reset(token)
            current_client.reset(client_token)
            current_identity.reset(identity_token)
            log.info(
                "gateway.tool_call",
                tool=context.message.name,
                caller=caller,
                outcome=outcome,
                ms=int((time.monotonic() - started) * 1000),
            )
