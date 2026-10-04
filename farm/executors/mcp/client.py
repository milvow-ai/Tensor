"""FastMCP client executor for MCP resource pools.

Mounts MCP connections (remote with OAuth token storage, stdio, or local FastMCP servers),
translates capability requests to MCP tool invocations, and maps MCP errors and payloads
to standardized ErrorKind values.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import structlog
from fastmcp import Client, FastMCP
from fastmcp.client.auth.oauth import OAuth
from fastmcp.exceptions import ToolError
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.executors.mcp.mapping import get_capability_mapping, map_request_params, map_tool_result
from farm.secrets import ensure_token_store_dir, redact, resolve_auth, resolve_token_store

logger = structlog.get_logger(__name__)


class McpExecutor:
    """Per-connection FastMCP client executor."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or Path(os.environ.get("FARM_DATA_DIR", "D:/farm-data"))
        self._clients: dict[str, Client[Any]] = {}

    def get_token_store_dir(self, connection: ConnectionView) -> Path:
        """Resolve and ensure the token store directory for a connection."""
        if connection.auth_ref.startswith("token-store:"):
            return resolve_token_store(connection.auth_ref)
        store_dir = self._data_dir / "tokens" / connection.id
        return ensure_token_store_dir(store_dir)

    def _create_client(self, connection: ConnectionView) -> Client[Any]:
        """Create a FastMCP Client for a given connection."""
        meta = connection.meta

        # In-memory / test server passed directly in meta
        if "server" in meta:
            server = meta["server"]
            if isinstance(server, FastMCP):
                return Client(server)
            return Client(transport=server)

        # Pre-configured transport
        if "transport" in meta:
            return Client(transport=meta["transport"])

        # Remote server URL
        server_url = meta.get("server_url") or meta.get("url")
        if not server_url:
            raise ValueError(
                f"Connection '{connection.id}' is missing 'server_url' in meta for MCP executor"
            )

        auth: Any = None
        if connection.auth_ref.startswith("token-store:"):
            token_dir = self.get_token_store_dir(connection)
            key_val_store = FileTreeStore(
                data_directory=str(token_dir),
                key_sanitization_strategy=AlwaysHashStrategy(),
                collection_sanitization_strategy=AlwaysHashStrategy(),
            )
            scopes = meta.get("scopes", ["mcp"])
            auth = OAuth(
                mcp_url=server_url,
                scopes=scopes,
                client_name=meta.get("client_name", f"Harness Farm ({connection.id})"),
                token_storage=key_val_store,
                client_id=meta.get("client_id"),
                client_secret=meta.get("client_secret"),
            )
        elif connection.auth_ref.startswith("env:"):
            auth = resolve_auth(connection.auth_ref)

        return Client(server_url, auth=auth)

    def _get_or_create_client(self, connection: ConnectionView) -> Client[Any]:
        """Retrieve existing client or instantiate a new one."""
        server_id = id(connection.meta.get("server")) if "server" in connection.meta else 0
        cache_key = f"{connection.id}_{server_id}"
        if cache_key not in self._clients:
            self._clients[cache_key] = self._create_client(connection)
        return self._clients[cache_key]

    async def execute(self, req: ExecRequest) -> ExecResult:
        """Execute a capability request against the mapped MCP tool."""
        start_time = time.perf_counter()
        conn = req.connection
        tool_map = conn.meta.get("tool_map")
        mapping = get_capability_mapping(tool_map, req.capability)

        tool_name = mapping.tool if mapping else req.capability
        tool_args = map_request_params(mapping.params if mapping else None, req.params)

        try:
            client = self._get_or_create_client(conn)
        except Exception as e:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.BAD_REQUEST,
                error=redact(str(e)),
                latency_ms=latency_ms,
            )

        try:
            async with asyncio.timeout(req.timeout_s):
                async with client:
                    call_res = await client.call_tool(tool_name, tool_args)
        except TimeoutError:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.TIMEOUT,
                error=f"MCP tool '{tool_name}' timed out after {req.timeout_s}s",
                latency_ms=latency_ms,
            )
        except ToolError as e:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return self._classify_error(str(e), conn.id, latency_ms)
        except Exception as e:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return self._classify_error(str(e), conn.id, latency_ms)

        latency_ms = int((time.perf_counter() - start_time) * 1000)

        # If the MCP tool result marked is_error=True
        if call_res.is_error:
            err_text = self._extract_error_text(call_res)
            return self._classify_error(err_text, conn.id, latency_ms)

        raw_data = self._extract_data(call_res)

        # Check for tool error payload in the response body
        if isinstance(raw_data, dict):
            payload_err = self._check_error_payload(raw_data, conn.id, latency_ms)
            if payload_err is not None:
                return payload_err

        # Map tool output to capability data
        mapped_data = map_tool_result(mapping.result if mapping else None, raw_data)

        # Determine found status
        found: bool | None = None
        if isinstance(raw_data, dict) and "found" in raw_data:
            found = bool(raw_data["found"])
        elif isinstance(mapped_data, dict):
            found = bool(mapped_data)
        elif isinstance(mapped_data, list):
            found = len(mapped_data) > 0
        elif mapped_data is None:
            found = False
        else:
            found = True

        # Extract units used
        units_used: dict[str, float] = {}
        if isinstance(raw_data, dict):
            if "credits_used" in raw_data:
                try:
                    units_used["credits"] = float(raw_data["credits_used"])
                except (ValueError, TypeError):
                    pass
            elif "units_used" in raw_data and isinstance(raw_data["units_used"], dict):
                for k, v in raw_data["units_used"].items():
                    try:
                        units_used[k] = float(v)
                    except (ValueError, TypeError):
                        pass

        result_dict = mapped_data if isinstance(mapped_data, dict) else {"result": mapped_data}

        return ExecResult(
            ok=True,
            data=result_dict,
            found=found,
            units_used=units_used,
            latency_ms=latency_ms,
        )

    def _extract_data(self, call_res: Any) -> Any:
        """Extract structured data or text content from CallToolResult."""
        if getattr(call_res, "data", None) is not None:
            return call_res.data
        if getattr(call_res, "structured_content", None) is not None:
            return call_res.structured_content
        content = getattr(call_res, "content", None)
        if content:
            texts = [c.text for c in content if hasattr(c, "text") and c.text]
            if texts:
                full_text = "\n".join(texts)
                try:
                    return json.loads(full_text)
                except Exception:
                    return full_text
        return None

    def _extract_error_text(self, call_res: Any) -> str:
        """Extract an error description from CallToolResult."""
        data = self._extract_data(call_res)
        if isinstance(data, dict):
            return str(data.get("error") or data.get("message") or json.dumps(data))
        if data:
            return str(data)
        return "MCP tool returned an error"

    def _check_error_payload(
        self, payload: dict[str, Any], conn_id: str, latency_ms: int
    ) -> ExecResult | None:
        """Check if an output dict represents a business or execution failure."""
        is_error = False
        err_msg = ""
        code = str(payload.get("code") or payload.get("error_code") or "").upper()
        status = str(payload.get("status") or "").lower()

        if "error" in payload and payload["error"]:
            is_error = True
            err_msg = str(payload["error"])
        elif status == "error":
            is_error = True
            err_msg = str(payload.get("message") or payload.get("error") or "Unknown error")
        elif code in ("CREDITS_EXHAUSTED", "LIMIT_REACHED", "UNAUTHORIZED", "RATE_LIMITED"):
            is_error = True
            err_msg = str(payload.get("message") or payload.get("error") or code)

        if payload.get("credits_remaining") == 0 and (
            "exhausted" in status or "insufficient" in err_msg.lower() or code == "CREDITS_EXHAUSTED"
        ):
            is_error = True
            if not err_msg:
                err_msg = "Credits exhausted"

        if not is_error:
            return None

        combined = f"{code} {err_msg}".strip()
        return self._classify_error(combined, conn_id, latency_ms)

    def _classify_error(self, err_msg: str, conn_id: str, latency_ms: int) -> ExecResult:
        """Classify an error message string into an ExecResult with appropriate ErrorKind."""
        cleaned = redact(err_msg)
        lowered = err_msg.lower()

        # Auth / expired token patterns -> NEEDS_LOGIN
        auth_signals = (
            "unauthorized",
            "token expired",
            "expired token",
            "auth failed",
            "authentication required",
            "authentication failed",
            "invalid token",
            "needs login",
            "not logged in",
            "please log in",
            "401",
            "forbidden",
            "403",
        )
        if any(sig in lowered for sig in auth_signals):
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.NEEDS_LOGIN,
                error=(
                    f"Authentication failed or token expired for connection '{conn_id}'."
                    f" Run: farm mcp login {conn_id}"
                ),
                latency_ms=latency_ms,
            )

        # Credit / quota patterns -> LIMIT_REACHED
        limit_signals = (
            "credits_exhausted",
            "credit balance",
            "insufficient credits",
            "credit limit",
            "limit reached",
            "limit_reached",
            "quota exceeded",
            "quota_exceeded",
            "out of credits",
            "no credits remaining",
            "exhausted",
        )
        if any(sig in lowered for sig in limit_signals):
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.LIMIT_REACHED,
                error=cleaned,
                latency_ms=latency_ms,
            )

        # Rate limited patterns -> RATE_LIMITED
        rate_signals = ("rate limit", "too many requests", "429")
        if any(sig in lowered for sig in rate_signals):
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.RATE_LIMITED,
                error=cleaned,
                latency_ms=latency_ms,
            )

        # Empty / Not found patterns -> EMPTY
        empty_signals = ("not found", "no results", "404")
        if any(sig in lowered for sig in empty_signals):
            return ExecResult(
                ok=False,
                found=False,
                error_kind=ErrorKind.EMPTY,
                error=cleaned,
                latency_ms=latency_ms,
            )

        # Bad request / validation patterns -> BAD_REQUEST
        bad_req_signals = ("invalid params", "invalid argument", "missing required", "validation error")
        if any(sig in lowered for sig in bad_req_signals):
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.BAD_REQUEST,
                error=cleaned,
                latency_ms=latency_ms,
            )

        # Default to SERVER
        return ExecResult(
            ok=False,
            error_kind=ErrorKind.SERVER,
            error=cleaned,
            latency_ms=latency_ms,
        )
