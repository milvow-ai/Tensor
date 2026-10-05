"""One call, or one listing, through a connection's client, and how either can fail.

A server's own answer is never a failure here: a tool that reports ``isError`` is a result the caller gets
as it is, and a JSON-RPC error that rejects the request itself (unknown tool, invalid params) is relayed the
same way. What fails is the *path* to the server: it cannot be started or reached, the credentials are
refused, the account is rate limited or out of quota, or the answer does not come in time. Those become an
:class:`~farm.executors.mcp.connect.McpFailure` with the ``ErrorKind`` the router acts on (next account,
cooldown, circuit, login alert). Messages never hold a secret: they go through ``redact``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx2
import mcp_types
from fastmcp import Client
from mcp.shared.exceptions import MCPError

from farm.executors.base import ErrorKind
from farm.executors.mcp.connect import HttpFailure, LoginRequired, McpFailure, retry_after_s
from farm.secrets import redact

MAX_MESSAGE_CHARS = 300

REJECTING_CODES = frozenset(
    {mcp_types.PARSE_ERROR, mcp_types.INVALID_REQUEST, mcp_types.METHOD_NOT_FOUND, mcp_types.INVALID_PARAMS}
)
"""JSON-RPC errors that say "this request is wrong" (the same on every account), so they are relayed."""

SDK_STAND_INS = frozenset({"Server returned an error response", "Not Found", "Session terminated"})
"""What the MCP SDK writes when an HTTP error answer carried no JSON-RPC error of its own."""


def _clip(text: str) -> str:
    flat = " ".join(redact(text).split())
    return flat if len(flat) <= MAX_MESSAGE_CHARS else flat[: MAX_MESSAGE_CHARS - 1] + "…"


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """``exc``, its causes, its context and the members of exception groups (FastMCP runs sessions in task
    groups, so the real error is often several levels down)."""
    seen: set[int] = set()
    pending = [exc]
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def from_status(failure: HttpFailure, *, oauth: bool) -> McpFailure:
    """What an HTTP error answer means for the account that got it."""
    status = failure.status
    if status in (401, 403):
        kind = ErrorKind.NEEDS_LOGIN if oauth else ErrorKind.AUTH
        return McpFailure(kind, f"the server refused the credentials (HTTP {status})")
    if status == 402:
        return McpFailure(
            ErrorKind.LIMIT_REACHED, "the server says the plan or credits are used up (HTTP 402)"
        )
    if status == 429:
        return McpFailure(
            ErrorKind.RATE_LIMITED,
            "the server is rate limiting this account (HTTP 429)",
            retry_after_s=failure.retry_after_s,
        )
    if status in (408, 504):
        return McpFailure(ErrorKind.TIMEOUT, f"the server did not answer in time (HTTP {status})")
    if status in (404, 405, 410):
        return McpFailure(
            ErrorKind.SERVER, f"the MCP endpoint was not found (HTTP {status}): check the provider's url"
        )
    if status >= 500:
        return McpFailure(ErrorKind.SERVER, f"the server failed (HTTP {status})")
    return McpFailure(ErrorKind.BAD_REQUEST, f"the server rejected the request (HTTP {status})")


def _is_stand_in(error: MCPError) -> bool:
    return error.error.message in SDK_STAND_INS


def classify(exc: BaseException, *, oauth: bool = False, http: HttpFailure | None = None) -> McpFailure:
    """How a failed attempt is reported to the router (see the module docstring).

    ``http`` is the last HTTP error answer of the attempt (``HttpStatusTap``): the SDK hides its status.
    """
    chain = list(_chain(exc))
    for member in chain:
        if isinstance(member, McpFailure):
            return member
        if isinstance(member, LoginRequired):
            return McpFailure(ErrorKind.NEEDS_LOGIN, str(member))
    for member in chain:
        if isinstance(member, httpx2.HTTPStatusError):
            failure = HttpFailure(member.response.status_code, retry_after_s(member.response))
            return from_status(failure, oauth=oauth)
    if http is not None and any(isinstance(m, MCPError | RuntimeError) for m in chain):
        return from_status(http, oauth=oauth)
    for member in chain:
        if isinstance(member, TimeoutError | httpx2.TimeoutException):
            return McpFailure(ErrorKind.TIMEOUT, "no answer from the server in time")
    for member in chain:
        if isinstance(member, MCPError):
            return McpFailure(
                ErrorKind.SERVER, f"the server answered with a protocol error ({_clip(member.error.message)})"
            )
    for member in chain:
        if isinstance(member, OSError | httpx2.TransportError):
            return McpFailure(ErrorKind.SERVER, f"could not reach the server: {_clip(str(member))}")
    return McpFailure(ErrorKind.SERVER, f"{type(exc).__name__}: {_clip(str(exc))}")


def rejecting_error(exc: BaseException) -> MCPError | None:
    """The JSON-RPC error that rejects the request itself (relayed to the caller, same on every account).

    Not the SDK's stand-ins for an HTTP error answer: those say nothing about the request.
    """
    for member in _chain(exc):
        if isinstance(member, MCPError) and member.error.code in REJECTING_CODES and not _is_stand_in(member):
            return member
    return None


async def call_tool(
    client: Client[Any], name: str, arguments: dict[str, Any], *, timeout_s: float
) -> mcp_types.CallToolResult:
    """``tools/call`` on a fresh or kept-alive session: the server's own ``CallToolResult``, untouched."""
    async with asyncio.timeout(timeout_s), client:
        return await client.call_tool_mcp(name, arguments)


async def list_tools(client: Client[Any], *, timeout_s: float) -> list[mcp_types.Tool]:
    """Every tool of the server (``tools/list`` follows the server's pagination cursors)."""
    async with asyncio.timeout(timeout_s), client:
        return await client.list_tools()
