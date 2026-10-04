"""Fake MCP servers for testing Harness Farm MCP executor and pools.

Provides a FakeClayServer mimicking Clay's MCP tools, error conditions,
and credit signals, as well as a generic MCP server for non-Clay capability testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError


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
