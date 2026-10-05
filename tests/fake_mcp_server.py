"""Fake MCP servers for testing Harness Farm MCP executor and pools.

Provides a FakeClayServer mimicking Clay's MCP tools, error conditions,
and credit signals, as well as a generic MCP server for non-Clay capability testing.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import McpError, ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from mcp_types import (
    INVALID_PARAMS,
    AudioContent,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    TextResourceContents,
)


@dataclass
class FakeServerState:
    auth_error: bool = False
    auth_error_message: str = "Unauthorized: access token expired"
    credits_exhausted: bool = False
    credits_remaining: int = 100
    rate_limit_error: bool = False
    server_error: bool = False
    custom_error: str | None = None
    recorded_calls: list[dict[str, Any]] = field(default_factory=list)


def create_fake_clay_server(name: str = "fake-clay") -> FastMCP:
    """Create a FastMCP server exposing Clay's tool surface with controllable state."""
    server = FastMCP(name)
    state = FakeServerState()
    server.state = state  # type: ignore[attr-defined]

    def _check_state(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        state.recorded_calls.append({"tool": tool_name, "args": args})

        if state.auth_error:
            raise ToolError(state.auth_error_message)

        if state.rate_limit_error:
            raise ToolError("Rate limit exceeded: 429 Too Many Requests")

        if state.server_error:
            raise ToolError("Internal Server Error: 500")

        if state.custom_error:
            raise ToolError(state.custom_error)

        if state.credits_exhausted or state.credits_remaining <= 0:
            return {
                "error": "Credit balance is 0. Insufficient credits to run tool.",
                "code": "CREDITS_EXHAUSTED",
                "credits_remaining": 0,
            }

        state.credits_remaining -= 1
        return None

    @server.tool(name="search-contacts")
    def search_contacts(
        query: str = "",
        name: str = "",
        company: str = "",
        domain: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        err = _check_state(
            "search-contacts",
            {"query": query, "name": name, "company": company, "domain": domain, "limit": limit},
        )
        if err:
            return err
        person_name = name or query or "Alice Smith"
        return {
            "contacts": [
                {
                    "id": "cont_01",
                    "name": person_name,
                    "first_name": person_name.split()[0] if person_name else "",
                    "last_name": person_name.split()[-1] if len(person_name.split()) > 1 else "",
                    "email": f"{person_name.lower().replace(' ', '.')}@example.com",
                    "title": "Head of Engineering",
                    "company": company or "Acme Corp",
                    "domain": domain or "acme.com",
                }
            ],
            "total": 1,
            "credits_used": 1,
        }

    @server.tool(name="search-companies")
    def search_companies(
        query: str = "",
        name: str = "",
        domain: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        err = _check_state(
            "search-companies",
            {"query": query, "name": name, "domain": domain, "limit": limit},
        )
        if err:
            return err
        company_name = name or query or "Acme Corp"
        return {
            "companies": [
                {
                    "id": "comp_01",
                    "name": company_name,
                    "domain": domain or "acme.com",
                    "headcount": 120,
                    "industry": "Software",
                }
            ],
            "total": 1,
            "credits_used": 1,
        }

    @server.tool(name="search-contacts-by-name")
    def search_contacts_by_name(name: str = "", company: str = "") -> dict[str, Any]:
        err = _check_state("search-contacts-by-name", {"name": name, "company": company})
        if err:
            return err
        email = (
            f"{name.lower().replace(' ', '.')}@{company.lower()}.com"
            if company
            else "user@example.com"
        )
        return {
            "contacts": [
                {
                    "id": "cont_02",
                    "name": name,
                    "company": company,
                    "email": email,
                }
            ],
            "credits_used": 1,
        }

    @server.tool(name="add-company-data-points")
    def add_company_data_points(domain: str, data_points: dict[str, Any]) -> dict[str, Any]:
        err = _check_state(
            "add-company-data-points", {"domain": domain, "data_points": data_points}
        )
        if err:
            return err
        return {"ok": True, "domain": domain, "updated": True, "credits_used": 1}

    @server.tool(name="add-contact-data-points")
    def add_contact_data_points(contact_id: str, data_points: dict[str, Any]) -> dict[str, Any]:
        err = _check_state(
            "add-contact-data-points", {"contact_id": contact_id, "data_points": data_points}
        )
        if err:
            return err
        return {"ok": True, "contact_id": contact_id, "updated": True, "credits_used": 1}

    @server.tool(name="query-objects")
    def query_objects(object_type: str, query: str = "") -> dict[str, Any]:
        err = _check_state("query-objects", {"object_type": object_type, "query": query})
        if err:
            return err
        return {"object_type": object_type, "results": [], "credits_used": 1}

    @server.tool(name="list_subroutines")
    def list_subroutines() -> dict[str, Any]:
        err = _check_state("list_subroutines", {})
        if err:
            return err
        return {"subroutines": [{"id": "sub_1", "name": "Enrich Lead"}]}

    @server.tool(name="run_subroutine")
    def run_subroutine(subroutine_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        err = _check_state("run_subroutine", {"subroutine_id": subroutine_id, "inputs": inputs})
        if err:
            return err
        return {
            "subroutine_id": subroutine_id,
            "status": "completed",
            "output": {"enriched": True},
            "credits_used": 2,
        }

    @server.tool(name="get-current-workspace")
    def get_current_workspace() -> dict[str, Any]:
        err = _check_state("get-current-workspace", {})
        if err:
            return err
        return {"workspace_id": f"ws_{name}", "name": f"Workspace {name}"}

    return server


def create_fake_generic_server(name: str = "fake-generic") -> FastMCP:
    """Create a generic non-Clay FastMCP server."""
    server = FastMCP(name)
    state = FakeServerState()
    server.state = state  # type: ignore[attr-defined]

    @server.tool(name="echo")
    def echo(message: str) -> dict[str, Any]:
        state.recorded_calls.append({"tool": "echo", "args": {"message": message}})
        return {"echoed": message}

    @server.tool(name="weather_service")
    def weather_service(city: str) -> dict[str, Any]:
        state.recorded_calls.append({"tool": "weather_service", "args": {"city": city}})
        return {
            "city": city,
            "temperature": 21,
            "conditions": "Partly Cloudy",
            "source": "fake-weather",
        }

    return server


# --- OPEN1: a rich generic server for pass-through tests -------------------------------------------------

FAKE_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nopen1-test-image").decode("ascii")
FAKE_WAV_B64 = base64.b64encode(b"RIFF\x00\x00\x00\x00WAVEopen1").decode("ascii")


@dataclass
class RichServerState:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """``(tool, arguments)`` exactly as the server received them."""


class _RejectsAtTheProtocolLevel(Middleware):
    """Answers ``tools/call`` of ``rejected`` with a JSON-RPC error (invalid params), as strict servers do."""

    async def on_call_tool(self, context: Any, call_next: Any) -> Any:
        if context.message.name == "rejected":
            raise McpError(INVALID_PARAMS, "the server rejected this request")
        return await call_next(context)


def create_fake_rich_server(name: str = "fake-rich", *, page_size: int | None = None) -> FastMCP:
    """A server with every kind of answer a client has to relay: text, image, audio, embedded resource,
    resource link, ``structuredContent``, ``_meta``, an ``isError`` tool, a slow tool, a tool whose output
    violates its own schema, and ``whoami`` (the server's name, to see which account answered)."""
    server = FastMCP(name, **({"list_page_size": page_size} if page_size else {}))
    state = RichServerState()
    server.state = state  # type: ignore[attr-defined]

    @server.tool(name="echo", title="Echo", annotations={"readOnlyHint": True, "idempotentHint": True})
    def echo(message: str) -> str:
        """Echo the message back."""
        state.calls.append(("echo", {"message": message}))
        return f"echo: {message}"

    @server.tool(name="add")
    def add(a: int, b: int) -> int:
        """Add two integers."""
        state.calls.append(("add", {"a": a, "b": b}))
        return a + b

    @server.tool(name="rich_echo")
    def rich_echo(message: str) -> ToolResult:
        """Answer with text, an image, audio, an embedded resource and a resource link."""
        state.calls.append(("rich_echo", {"message": message}))
        return ToolResult(
            content=[
                TextContent(type="text", text=f"rich: {message}"),
                ImageContent(type="image", data=FAKE_PNG_B64, mime_type="image/png"),
                AudioContent(type="audio", data=FAKE_WAV_B64, mime_type="audio/wav"),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(uri="memo://note", text="a note", mime_type="text/plain"),
                ),
                ResourceLink(type="resource_link", name="Doc", uri="https://example.com/doc"),
            ],
            structured_content={"message": message, "blocks": 5},
            meta={"origin": name},
        )

    @server.tool(name="soft_error")
    def soft_error(detail: str) -> ToolResult:
        """Report a failure of its own (isError), not a protocol error."""
        state.calls.append(("soft_error", {"detail": detail}))
        return ToolResult(
            content=[TextContent(type="text", text=f"remote failure: {detail}")],
            structured_content={"detail": detail},
            is_error=True,
        )

    @server.tool(name="slow")
    async def slow(seconds: float = 1.0) -> str:
        """Take a while to answer."""
        state.calls.append(("slow", {"seconds": seconds}))
        await asyncio.sleep(seconds)
        return "done"

    @server.tool(
        name="schema_violation",
        output_schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
    )
    def schema_violation() -> ToolResult:
        """Declares an integer result and returns a string (a server that breaks its own contract)."""
        state.calls.append(("schema_violation", {}))
        return ToolResult(content=[TextContent(type="text", text="n is x")], structured_content={"n": "x"})

    @server.tool(name="whoami")
    def whoami() -> str:
        """The name of this server."""
        state.calls.append(("whoami", {}))
        return name

    @server.tool(name="rejected")
    def rejected() -> str:
        """A tool the server itself refuses at the protocol level (a JSON-RPC error, not an isError result)."""
        return "unreachable"

    server.add_middleware(_RejectsAtTheProtocolLevel())
    return server
