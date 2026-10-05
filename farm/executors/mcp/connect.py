"""From a connection's configuration to a FastMCP client: stdio, http and sse servers, with an account each.

The server definition is the provider's ``mcp`` block (registry) plus what the connection overrides in
``meta.mcp``; the account's own credential is its ``auth_ref``. The building blocks are FastMCP's:

* ``fastmcp.mcp_config`` (``StdioMCPServer`` / ``RemoteMCPServer``, the format Claude and Codex configs use)
  turns the definition into the stdio / streamable-http / sse transport;
* ``fastmcp.client.auth.oauth.OAuth`` does OAuth, with tokens in the connection's own token store;
* every transport is wrapped in :class:`RelayTransport`, so the Farm relays what a server answers without
  re-validating it (the ``ProxyClient`` session) and without forwarding the caller's HTTP headers (a
  ``ProxyClient`` would send the Farm's own bearer token to every upstream server).

Secrets are resolved here and only here, at connect time: ``env:NAME`` references become values for the
child process or the request headers and are registered for redaction by ``resolve_auth``.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol, Unpack

import httpx2
import mcp_types
from fastmcp import Client, FastMCP
from fastmcp.client.auth.oauth import OAuth
from fastmcp.client.transports import (
    ClientTransport,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
    infer_transport,
)
from fastmcp.client.transports.base import SessionKwargs, TransportOptions
from fastmcp.mcp_config import RemoteMCPServer
from fastmcp.server.providers.proxy import PROXY_TRANSPORT_OPTIONS
from key_value.aio.protocols import AsyncKeyValue
from mcp import ClientSession

from farm import __version__
from farm.executors.base import ConnectionView, ErrorKind
from farm.registry.models import McpConnectionOverride, McpProviderSpec
from farm.secrets import AuthRefError, resolve_auth

RELAY_OPTIONS = replace(PROXY_TRANSPORT_OPTIONS, forward_incoming_headers=False)
"""FastMCP's proxy session (no re-validation of results against the server's own output schema), minus the
forwarding of the caller's headers."""

CLIENT_NAME = "harness-farm"

type TransportFactory = Callable[[ConnectionView, McpProviderSpec], ClientTransport | FastMCP[Any]]
"""Builds the transport (or an in-process FastMCP server, for tests) of one connection."""


class McpDirectory(Protocol):
    """Where the executor finds a provider's ``mcp`` block (the database in production)."""

    async def mcp_spec(self, provider_id: str) -> McpProviderSpec | None: ...


class McpFailure(Exception):
    """A call or a listing failed in a way the router should act on. The message never holds a secret."""

    def __init__(self, kind: ErrorKind, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after_s = retry_after_s


class LoginRequired(RuntimeError):
    """OAuth needs a person: the account has no usable token (``farm mcp login <connection>``)."""


class HeadlessOAuth(OAuth):
    """OAuth that never opens a browser: serving a call must not hang on an interactive login."""

    async def redirect_handler(self, authorization_url: str) -> None:
        raise LoginRequired("this account has no usable OAuth token; log it in with `farm mcp login`")


@dataclass(frozen=True)
class HttpFailure:
    """An HTTP error answer from the server: its status and, for a 429, how long it asked us to wait."""

    status: int
    retry_after_s: float | None = None


def retry_after_s(response: httpx2.Response) -> float | None:
    """The ``Retry-After`` header of ``response`` in seconds (it may be a number or an HTTP date)."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class HttpStatusTap:
    """Remembers the last HTTP error answer to a JSON-RPC request of an http / sse transport.

    The MCP SDK reduces every HTTP error answer to the same JSON-RPC error ("Server returned an error
    response"), so the status that tells the router what to do (401: log in again, 402: out of quota, 429:
    wait, 5xx: the server is failing) is read off the wire here, with an httpx response hook.
    """

    def __init__(self) -> None:
        self._last: HttpFailure | None = None

    async def _record(self, response: httpx2.Response) -> None:
        if response.status_code >= 400 and response.request.method == "POST":
            self._last = HttpFailure(response.status_code, retry_after_s(response))

    def clear(self) -> None:
        self._last = None

    def take(self) -> HttpFailure | None:
        last, self._last = self._last, None
        return last

    def install(self, transport: ClientTransport) -> None:
        """Make an http / sse transport report its error statuses here (other transports have none)."""
        if isinstance(transport, StreamableHttpTransport | SSETransport):
            transport.httpx_client_factory = self._wrap(transport.httpx_client_factory)

    def _wrap(self, factory: Callable[..., httpx2.AsyncClient] | None) -> Callable[..., httpx2.AsyncClient]:
        def build(
            headers: dict[str, str] | None = None,
            timeout: httpx2.Timeout | None = None,
            auth: httpx2.Auth | None = None,
            **kwargs: Any,
        ) -> httpx2.AsyncClient:
            if factory is not None:
                client = factory(headers=headers, timeout=timeout, auth=auth, **kwargs)
            else:
                client = httpx2.AsyncClient(
                    headers=headers, timeout=timeout or httpx2.Timeout(30.0, read=300.0), auth=auth, **kwargs
                )
            client.event_hooks.setdefault("response", []).append(self._record)
            return client

        return build


class RelayTransport(ClientTransport):
    """Wraps a transport so its sessions relay results untouched (see the module docstring)."""

    def __init__(self, inner: ClientTransport) -> None:
        self.inner = inner
        self.legacy_only = inner.legacy_only
        self.http_status = HttpStatusTap()
        self.http_status.install(inner)

    @contextlib.asynccontextmanager
    async def connect_session(
        self,
        *,
        transport_options: TransportOptions | None = None,
        **session_kwargs: Unpack[SessionKwargs],
    ) -> AsyncIterator[ClientSession]:
        options = replace(
            transport_options or TransportOptions(),
            session_class=RELAY_OPTIONS.session_class,
            forward_incoming_headers=RELAY_OPTIONS.forward_incoming_headers,
        )
        async with self.inner.connect_session(transport_options=options, **session_kwargs) as session:
            yield session

    async def close(self) -> None:
        await self.inner.close()  # type: ignore[no-untyped-call]  # FastMCP's close() is not annotated

    def get_session_id(self) -> str | None:
        return self.inner.get_session_id()

    def __repr__(self) -> str:
        return f"<RelayTransport({self.inner!r})>"


def _resolve_refs(refs: Mapping[str, str]) -> dict[str, str]:
    """``{name: env:VAR}`` -> ``{name: value}``; a missing variable is an :class:`McpFailure` naming it."""
    try:
        return {name: resolve_auth(ref) for name, ref in refs.items()}
    except AuthRefError as exc:
        raise McpFailure(ErrorKind.NEEDS_LOGIN, f"{exc} (set it with `farm set-secret`)") from None


def build_transport(
    connection: ConnectionView,
    spec: McpProviderSpec,
    *,
    token_storage: Callable[[], AsyncKeyValue],
    interactive: bool = False,
) -> ClientTransport:
    """The transport that reaches ``spec`` as ``connection``: provider block + the connection's overrides.

    ``interactive`` (``farm mcp login`` only) lets OAuth open the browser; a serving Farm never does.
    """
    override = McpConnectionOverride.model_validate(connection.meta.get("mcp") or {})
    if spec.transport == "stdio":
        assert spec.command is not None  # the registry model guarantees it
        env = _resolve_refs({**spec.env, **override.env})
        # Only the configured variables reach the child (plus the SDK's minimal safe set): the Farm's own
        # environment holds every provider key and must not leak into a third-party server.
        # The server's stderr goes to its own log file, never into the Farm's console: a 24/7 Farm keeps
        # one log per connection, and a captured parent stdio (no fileno, e.g. under pytest) cannot break
        # the launch.
        log_dir = Path(os.environ.get("FARM_DATA_DIR", "D:/farm-data")) / "logs" / "mcp"
        log_dir.mkdir(parents=True, exist_ok=True)
        return StdioTransport(
            command=spec.command,
            args=list(spec.args),
            env=dict(env),
            cwd=spec.cwd,
            log_file=log_dir / f"{connection.id}.log",
        )

    assert spec.url is not None
    url = _resolve_refs({"url": spec.url})["url"] if spec.url.startswith("env:") else spec.url
    headers = _resolve_refs({**spec.headers, **override.headers})
    auth: str | OAuth | None = None
    if spec.auth == "env":
        auth = resolve_auth(connection.auth_ref)
    elif spec.auth == "oauth":
        oauth_class = OAuth if interactive else HeadlessOAuth
        auth = oauth_class(
            mcp_url=url, token_storage=token_storage(), client_name=f"Harness Farm ({connection.id})"
        )
    return RemoteMCPServer(url=url, transport=spec.transport, headers=headers, auth=auth).to_transport()


async def close_client(client: Client[Any]) -> None:
    """Disconnect a client and stop the server process it started."""
    await client.close()  # type: ignore[no-untyped-call]  # FastMCP's close() is not annotated


def build_client(target: ClientTransport | FastMCP[Any], connection_id: str) -> Client[RelayTransport]:
    """A client for ``target`` that relays results as the server sent them, introducing itself as the Farm."""
    return Client(
        RelayTransport(infer_transport(target)),
        name=f"{CLIENT_NAME}:{connection_id}",
        client_info=mcp_types.Implementation(name=CLIENT_NAME, version=__version__),
    )
