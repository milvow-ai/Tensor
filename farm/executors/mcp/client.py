"""FastMCP client executor for MCP resource pools.

Two ways in, one client per connection (account):

* **typed capabilities** (``verify_email``, ``find_person``, ...): the capability is mapped to one MCP tool
  (``meta.tool_map``), the answer is mapped back, errors and payloads are classified into ``ErrorKind``;
* **pass-through** (capability ``mcp:<provider>``, OPEN1): any tool of any MCP server, called by its own name
  with the caller's own arguments. The server's ``CallToolResult`` comes back untouched, a tool that reports
  ``isError`` is a result (no failover), and only the path to the server can fail (see ``relay``).

A connection reaches its server through the provider's ``mcp`` block (registry; found through the
``directory``), or, for the typed capabilities of M3c-A, through ``meta`` (``server``, ``transport``,
``server_url``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import mcp_types
import structlog
from fastmcp import Client, FastMCP
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports import ClientTransport
from fastmcp.exceptions import ToolError
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore
from pydantic import SecretStr

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.executors.mcp import relay
from farm.executors.mcp.connect import (
    McpDirectory,
    McpFailure,
    RelayTransport,
    TransportFactory,
    build_client,
    build_transport,
    close_client,
)
from farm.executors.mcp.mapping import get_capability_mapping, map_request_params, map_tool_result
from farm.registry.models import MCP_CAPABILITY_PREFIX, McpProviderSpec
from farm.secrets import ensure_token_store_dir, redact, resolve_auth, resolve_token_store

logger = structlog.get_logger(__name__)

PASSTHROUGH_PREFIX = MCP_CAPABILITY_PREFIX
RESULT_KEY = "call_tool_result"
"""Key of the relayed ``CallToolResult`` in ``ExecResult.data`` (a wrapper, so no key the router reads
for its own purposes, such as ``model`` or ``session_id``, can collide with the server's)."""
REJECTION_KEY = "rpc_error"


def _fresh_attempt(client: Client[Any]) -> Client[Any]:
    """Forget the HTTP error answers of earlier attempts (the tap is read when this attempt fails)."""
    if isinstance(client.transport, RelayTransport):
        client.transport.http_status.clear()
    return client


class McpExecutor:
    """Per-connection FastMCP client executor."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        directory: McpDirectory | None = None,
        transports: TransportFactory | None = None,
    ) -> None:
        """``directory`` finds a provider's ``mcp`` block; ``transports`` replaces how its transport is built
        (tests hand in in-process servers; production leaves it alone)."""
        self._data_dir = data_dir or Path(os.environ.get("FARM_DATA_DIR", "D:/farm-data"))
        self._clients: dict[str, Client[Any]] = {}
        self._directory = directory
        self._transports = transports
        self._relays: dict[str, tuple[str, Client[Any]]] = {}
        """Pass-through clients: connection id -> (fingerprint of what they were built from, client)."""

    async def aclose(self) -> None:
        """Close every client (stdio servers are terminated); ``FarmContext.aclose`` calls this."""
        clients = [*self._clients.values(), *(client for _, client in self._relays.values())]
        self._clients.clear()
        self._relays.clear()
        await asyncio.gather(*(close_client(client) for client in clients), return_exceptions=True)

    def get_token_store_dir(self, connection: ConnectionView) -> Path:
        """Resolve and ensure the token store directory for a connection."""
        if connection.auth_ref.startswith("token-store:"):
            store_dir = resolve_token_store(connection.auth_ref, data_dir=self._data_dir)
        else:
            store_dir = self._data_dir / "tokens" / connection.id

        tokens_root = (self._data_dir / "tokens").resolve()
        resolved_store = store_dir.resolve()
        try:
            resolved_store.relative_to(tokens_root)
        except ValueError:
            raise ValueError(
                f"Path traversal detected: token store directory '{resolved_store}' "
                f"escapes tokens root '{tokens_root}'"
            ) from None
        if resolved_store == tokens_root:
            raise ValueError(
                f"Token store directory cannot be the tokens root itself: '{resolved_store}'"
            )
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
            scopes = meta.get("scopes", ["mcp"])
            auth = OAuth(
                mcp_url=server_url,
                scopes=scopes,
                client_name=meta.get("client_name", f"Harness Farm ({connection.id})"),
                token_storage=self.token_storage(connection),
                client_id=meta.get("client_id"),
                client_secret=meta.get("client_secret"),
            )
        elif connection.auth_ref.startswith("env:"):
            auth = resolve_auth(connection.auth_ref)

        return Client(server_url, auth=auth)

    def token_storage(self, connection: ConnectionView) -> FileTreeStore:
        """The connection's own OAuth token store (one directory per account, never shared)."""
        return FileTreeStore(
            data_directory=str(self.get_token_store_dir(connection)),
            key_sanitization_strategy=AlwaysHashStrategy(),
            collection_sanitization_strategy=AlwaysHashStrategy(),
        )

    def _get_or_create_client(self, connection: ConnectionView) -> Client[Any]:
        """Retrieve existing client or instantiate a new one."""
        server_id = id(connection.meta.get("server")) if "server" in connection.meta else 0
        cache_key = f"{connection.id}_{server_id}"
        if cache_key not in self._clients:
            self._clients[cache_key] = self._create_client(connection)
        return self._clients[cache_key]

    # --- pass-through (OPEN1) ----------------------------------------------------------------------------

    async def _spec(self, connection: ConnectionView) -> McpProviderSpec | None:
        if self._directory is None:
            return None
        return await self._directory.mcp_spec(connection.provider_id)

    async def _relay_client(self, connection: ConnectionView) -> Client[Any]:
        """The connection's relay client, built from its provider's ``mcp`` block and kept while that does
        not change (a stdio server stays running between calls; an edited block gets a fresh client)."""
        meta = connection.meta
        if "server" in meta or "transport" in meta:  # an in-process server or prepared transport (tests)
            target = meta.get("server") or meta["transport"]
            fingerprint = f"object:{id(target)}"
            spec = None
        else:
            spec = await self._spec(connection)
            if spec is None:
                raise McpFailure(
                    ErrorKind.BAD_REQUEST, f"provider '{connection.provider_id}' has no mcp configuration"
                )
            override = json.dumps(meta.get("mcp") or {}, sort_keys=True)
            source = f"{spec.model_dump_json()}|{override}|{connection.auth_ref}"
            fingerprint = hashlib.sha256(source.encode()).hexdigest()
        cached = self._relays.get(connection.id)
        if cached is not None and cached[0] == fingerprint:
            return _fresh_attempt(cached[1])
        if cached is not None:
            await self.discard(connection.id)
        built = build_client(target if spec is None else self._transport(connection, spec), connection.id)
        self._relays[connection.id] = (fingerprint, built)
        return _fresh_attempt(built)

    def _transport(
        self, connection: ConnectionView, spec: McpProviderSpec, *, interactive: bool = False
    ) -> ClientTransport | FastMCP[Any]:
        if self._transports is not None:
            return self._transports(connection, spec)
        return build_transport(
            connection,
            spec,
            token_storage=lambda: self.token_storage(connection),
            interactive=interactive,
        )

    async def login(self, connection: ConnectionView, *, timeout_s: float = 300.0) -> int:
        """Log an OAuth account in (``farm mcp login``): the browser opens, the tokens are stored in the
        account's own token store, and the number of tools the server then lists is returned."""
        spec = await self._spec(connection)
        if spec is None or spec.auth != "oauth":
            raise McpFailure(ErrorKind.BAD_REQUEST, "this connection does not use OAuth (mcp.auth: oauth)")
        client = build_client(self._transport(connection, spec, interactive=True), connection.id)
        try:
            tools = await relay.list_tools(client, timeout_s=timeout_s)
        except Exception as exc:
            raise relay.classify(exc, oauth=True) from exc
        finally:
            await close_client(client)
        await self.discard(connection.id)  # serving continues with a client that reads the stored tokens
        return len(tools)

    async def discard(self, connection_id: str) -> None:
        """Close and forget a connection's relay client (its next use starts a fresh server / session)."""
        cached = self._relays.pop(connection_id, None)
        if cached is not None:
            await asyncio.gather(close_client(cached[1]), return_exceptions=True)

    async def list_tools(
        self, connection: ConnectionView, *, timeout_s: float = 60.0
    ) -> list[mcp_types.Tool]:
        """Every tool the server lists through this connection (all pages). Raises :class:`McpFailure`."""
        client: Client[Any] | None = None
        try:
            client = await self._relay_client(connection)
            return await relay.list_tools(client, timeout_s=timeout_s)
        except Exception as exc:
            raise await self._failure(connection, client, exc) from exc

    async def _uses_oauth(self, connection: ConnectionView) -> bool:
        spec = await self._spec(connection) if "server" not in connection.meta else None
        return spec is not None and spec.auth == "oauth"

    async def _failure(
        self, connection: ConnectionView, client: Client[Any] | None, exc: Exception
    ) -> McpFailure:
        """How a failed attempt is reported; a client that failed is dropped (a dead or wedged server must
        not be reused, its next attempt starts a fresh one)."""
        transport = client.transport if client is not None else None
        http = transport.http_status.take() if isinstance(transport, RelayTransport) else None
        failure = relay.classify(exc, oauth=await self._uses_oauth(connection), http=http)
        if failure.kind is not ErrorKind.BAD_REQUEST:
            await self.discard(connection.id)
        return failure

    async def _passthrough(self, req: ExecRequest) -> ExecResult:
        """Call ``params['tool']`` with ``params['arguments']`` and relay the server's answer untouched."""
        started = time.perf_counter()
        conn = req.connection

        def failed(failure: McpFailure) -> ExecResult:
            return ExecResult(
                ok=False,
                error_kind=failure.kind,
                error=f"[{conn.id}] {failure.kind.value}: {failure.message}",
                retry_after_s=failure.retry_after_s,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        tool = req.params.get("tool")
        arguments = req.params.get("arguments")
        if isinstance(arguments, SecretStr):
            arguments = json.loads(arguments.get_secret_value())
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            return failed(McpFailure(ErrorKind.BAD_REQUEST, "a pass-through call needs a tool and arguments"))
        asked = req.params.get("timeout_s")
        timeout_s = min(req.timeout_s, float(asked)) if isinstance(asked, int | float) else req.timeout_s

        client: Client[Any] | None = None
        try:
            client = await self._relay_client(conn)
            result = await relay.call_tool(client, tool, arguments, timeout_s=timeout_s)
        except Exception as exc:
            rejection = relay.rejecting_error(exc)
            if rejection is not None:  # the server rejected the request itself: its answer, not a failure
                return ExecResult(
                    ok=True,
                    data={
                        REJECTION_KEY: {
                            "code": rejection.error.code,
                            "message": rejection.error.message,
                            "data": rejection.error.data,
                        }
                    },
                    units_used={"calls": 1.0},
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            failure = await self._failure(conn, client, exc)
            logger.warning("mcp.passthrough_failed", connection=conn.id, tool=tool, kind=failure.kind.value)
            return failed(failure)
        return ExecResult(
            ok=True,
            data={RESULT_KEY: result.model_dump(mode="json", by_alias=True, exclude_none=True)},
            units_used={"calls": 1.0},
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # --- typed capabilities (M3c-A) -----------------------------------------------------------------------

    async def _typed_client(self, connection: ConnectionView) -> Client[Any]:
        """The client of a typed capability: the provider's ``mcp`` block when it has one, else ``meta``."""
        if "server" not in connection.meta and "transport" not in connection.meta:
            if await self._spec(connection) is not None:
                return await self._relay_client(connection)
        return self._get_or_create_client(connection)

    async def execute(self, req: ExecRequest) -> ExecResult:
        """Run a pass-through call, or a typed capability through its mapped tool."""
        if req.capability.startswith(PASSTHROUGH_PREFIX):
            return await self._passthrough(req)
        start_time = time.perf_counter()
        conn = req.connection
        tool_map = conn.meta.get("tool_map")
        mapping = get_capability_mapping(tool_map, req.capability)

        tool_name = mapping.tool if mapping else req.capability
        tool_args = map_request_params(mapping.params if mapping else None, req.params)

        try:
            client = await self._typed_client(conn)
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
