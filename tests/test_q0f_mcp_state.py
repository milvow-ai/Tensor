"""Q0-03 and Q0-25: what changes an account's state in the MCP path, and whose evidence it is.

* Q0-03: a tool's own error (``isError``, or an answer that says ``error``) is a result. Only the path to the
  server (HTTP status, JSON-RPC codes, OAuth) may say "log in again", "out of credits" or "slow down".
* Q0-25: the HTTP status of a failed request belongs to the call that made it, and a failing call does not cut
  off its siblings on the same connection.

The HTTP tests run a real MCP server (streamable http, in process) behind an ASGI front that answers chosen
tool calls with an HTTP error, so the whole stack (SDK, client, tap, classifier) is the production one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.executors.mcp.client import RESULT_KEY, McpExecutor
from farm.registry.models import McpProviderSpec
from farm.resources.health import LOGIN_KINDS, NEUTRAL_KINDS

# --- Q0-03 -----------------------------------------------------------------------------------------------

TOOL_ERRORS = [
    "Company 4031 not found",  # contains 403
    "Order #14290 not found",  # contains 429
    "Invoice 40123 could not be parsed",  # contains 401
    "Contact 404 has no email",
    "Unauthorized: access token expired",
    "Rate limit exceeded: 429 Too Many Requests",
    "Credit balance is 0. Insufficient credits to run tool.",
    "Your quota is exhausted",
    "Internal Server Error: 500",
]


def _tool_server() -> FastMCP:
    server = FastMCP("q0f-tools")

    @server.tool(name="fail_with")
    def fail_with(text: str) -> str:
        raise ToolError(text)

    @server.tool(name="answer_error")
    def answer_error(text: str) -> dict[str, Any]:
        return {"error": text, "code": "UNAUTHORIZED"}

    return server


def _typed(capability: str, text: str) -> ExecRequest:
    conn = ConnectionView(
        id="acct-01", provider_id="acct", auth_ref="token-store:acct-01", meta={"server": _tool_server()}
    )
    return ExecRequest(request_id=uuid4(), capability=capability, params={"text": text}, connection=conn)


@pytest.mark.parametrize("capability", ["fail_with", "answer_error"])
@pytest.mark.parametrize("text", TOOL_ERRORS)
async def test_a_tools_own_error_never_changes_the_account(
    tmp_path: Path, capability: str, text: str
) -> None:
    result = await McpExecutor(data_dir=tmp_path).execute(_typed(capability, text))

    assert result.ok is False
    assert result.error_kind in NEUTRAL_KINDS, f"{text!r} became {result.error_kind}"
    assert result.error_kind not in LOGIN_KINDS
    assert result.retry_after_s is None and result.reset_at is None
    assert "tool reported an error" in str(result.error)


# --- an MCP server over HTTP with chosen failures ---------------------------------------------------------

type Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
type Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

URL = "http://127.0.0.1/mcp"


class _FailingFront:
    """ASGI front of an MCP server: a ``tools/call`` of a tool in ``plan`` is answered with that HTTP status
    (after ``gate`` is open: it opens when ``gate_size`` such requests have arrived, so they answer together)."""

    def __init__(self, app: Any, plan: dict[str, int], *, gate_size: int = 1) -> None:
        self.app = app
        self.plan = plan
        self.gate = asyncio.Barrier(gate_size)

    async def __call__(self, scope: MutableMapping[str, Any], receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        status = self._planned_status(body)
        if status is not None:
            await self.gate.wait()
            headers = [(b"retry-after", b"7")] if status == 429 else []
            await send({"type": "http.response.start", "status": status, "headers": headers})
            await send({"type": "http.response.body", "body": b"no"})
            return

        replayed = False

        async def replay() -> MutableMapping[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # then whatever the transport says next (a disconnect)

        await self.app(scope, replay, send)

    def _planned_status(self, body: bytes) -> int | None:
        try:
            message = json.loads(body)
        except ValueError:
            return None
        if isinstance(message, dict) and message.get("method") == "tools/call":
            return self.plan.get(str(message["params"]["name"]))
        return None


class _Directory:
    async def mcp_spec(self, provider_id: str) -> McpProviderSpec | None:
        return McpProviderSpec(transport="http", url=URL)


@contextlib.asynccontextmanager
async def _http_executor(
    tmp_path: Path, plan: dict[str, int], *, gate_size: int = 1
) -> AsyncIterator[McpExecutor]:
    server = FastMCP("q0f-http")

    @server.tool(name="slow")
    async def slow() -> str:
        await asyncio.sleep(0.6)
        return "done"

    @server.tool(name="limited")
    def limited() -> str:
        return "unreachable"

    @server.tool(name="broken")
    def broken() -> str:
        return "unreachable"

    app = server.http_app()
    front = _FailingFront(app, plan, gate_size=gate_size)

    def factory(**kwargs: Any) -> httpx2.AsyncClient:
        kwargs.pop("transport", None)
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=front), **kwargs)

    executor = McpExecutor(
        data_dir=tmp_path,
        directory=_Directory(),
        transports=lambda connection, spec: StreamableHttpTransport(URL, httpx_client_factory=factory),
    )
    async with app.router.lifespan_context(app):
        try:
            yield executor
        finally:
            await executor.aclose()


def _call(tool: str, *, typed: bool = False) -> ExecRequest:
    conn = ConnectionView(id="remote-01", provider_id="remote", auth_ref="cli:none", meta={})
    return ExecRequest(
        request_id=uuid4(),
        capability=tool if typed else "mcp:remote",
        params={} if typed else {"tool": tool, "arguments": {}},
        connection=conn,
        timeout_s=20,
    )


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, ErrorKind.AUTH), (402, ErrorKind.LIMIT_REACHED), (429, ErrorKind.RATE_LIMITED), (503, ErrorKind.SERVER)],
)
async def test_the_http_status_of_a_typed_call_still_decides_the_state(
    tmp_path: Path, status: int, kind: ErrorKind
) -> None:
    async with _http_executor(tmp_path, {"limited": status}) as executor:
        result = await executor.execute(_call("limited", typed=True))

    assert (result.ok, result.error_kind) == (False, kind)
    if status == 429:
        assert result.retry_after_s == 7.0


# --- Q0-25 -------------------------------------------------------------------------------------------------


async def test_concurrent_calls_each_get_their_own_http_status(tmp_path: Path) -> None:
    """Both error answers arrive together. With one shared slot the later one overwrote the earlier."""
    async with _http_executor(tmp_path, {"limited": 429, "broken": 503}, gate_size=2) as executor:
        limited, broken = await asyncio.gather(
            executor.execute(_call("limited")), executor.execute(_call("broken"))
        )

    assert (limited.error_kind, limited.retry_after_s) == (ErrorKind.RATE_LIMITED, 7.0)
    assert broken.error_kind is ErrorKind.SERVER


async def test_a_failing_call_does_not_cut_off_a_sibling_in_flight(tmp_path: Path) -> None:
    async with _http_executor(tmp_path, {"broken": 503}) as executor:
        slow = asyncio.create_task(executor.execute(_call("slow")))
        await asyncio.sleep(0.2)  # the slow call is running on the shared client
        broken = await executor.execute(_call("broken"))
        survivor: ExecResult = await slow
        first_client = next(iter(executor._retired), None)  # noqa: SLF001  (the failed client's lifecycle is the point)
        after = await executor.execute(_call("slow"))

    assert broken.error_kind is ErrorKind.SERVER
    assert survivor.ok is True, survivor.error
    assert survivor.data is not None and RESULT_KEY in survivor.data
    assert first_client is None  # closed when its last call ended
    assert after.ok is True  # the next call started a fresh client
