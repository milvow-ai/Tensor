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

import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol, Unpack
from urllib.parse import urlparse

import httpx2
import mcp_types
import structlog
from fastmcp import Client, FastMCP
from fastmcp.client.auth.oauth import OAuth, TokenStorageAdapter
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
from key_value.aio.adapters.pydantic import PydanticAdapter
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.memory import MemoryStore
from mcp import ClientSession
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    extract_field_from_www_auth,
    extract_resource_metadata_from_www_auth,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    issuers_match,
    validate_metadata_issuer,
)
from mcp.shared.auth import OAuthMetadata, OAuthToken, ProtectedResourceMetadata
from mcp.shared.inbound import MCP_PROTOCOL_VERSION_HEADER

from farm import __version__
from farm.executors.base import ConnectionView, ErrorKind
from farm.registry.models import McpConnectionOverride, McpProviderSpec
from farm.secrets import AuthRefError, redact, register_secret, resolve_auth
from farm.settings import data_dir_path

logger = structlog.get_logger(__name__)

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


class FarmTokenStorageAdapter(TokenStorageAdapter):
    """Token storage adapter that also persists and loads OAuth server metadata."""

    def __init__(
        self,
        async_key_value: AsyncKeyValue,
        server_url: str,
        cache_namespace: str | None = None,
    ) -> None:
        super().__init__(async_key_value, server_url, cache_namespace=cache_namespace)
        self._storage_oauth_metadata = PydanticAdapter[OAuthMetadata](
            default_collection="mcp-oauth-metadata",
            key_value=async_key_value,
            pydantic_model=OAuthMetadata,
            raise_on_validation_error=True,
        )
        self._storage_prm = PydanticAdapter[ProtectedResourceMetadata](
            default_collection="mcp-oauth-prm",
            key_value=async_key_value,
            pydantic_model=ProtectedResourceMetadata,
            raise_on_validation_error=True,
        )

    def _get_metadata_cache_key(self) -> str:
        return f"{self._cache_key_prefix()}/metadata"

    def _get_prm_cache_key(self) -> str:
        return f"{self._cache_key_prefix()}/prm"

    async def get_metadata(self) -> OAuthMetadata | None:
        res = await self._storage_oauth_metadata.get(key=self._get_metadata_cache_key())
        return res if isinstance(res, OAuthMetadata) else None

    async def set_metadata(self, metadata: OAuthMetadata) -> None:
        await self._storage_oauth_metadata.put(
            key=self._get_metadata_cache_key(),
            value=metadata,
            ttl=60 * 60 * 24 * 365,
        )

    async def get_prm(self) -> ProtectedResourceMetadata | None:
        res = await self._storage_prm.get(key=self._get_prm_cache_key())
        return res if isinstance(res, ProtectedResourceMetadata) else None

    async def set_prm(self, prm: ProtectedResourceMetadata) -> None:
        await self._storage_prm.put(
            key=self._get_prm_cache_key(),
            value=prm,
            ttl=60 * 60 * 24 * 365,
        )


def _origin_issuer(server_url: str) -> str:
    # RFC 8414 §3.3: origin issuer normalization; isolates SDK private helper _origin_issuer
    parsed = urlparse(server_url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def discover_metadata_from_server(
    server_url: str,
    httpx_client: httpx2.AsyncClient,
) -> tuple[ProtectedResourceMetadata | None, OAuthMetadata | None]:
    """Discover protected resource metadata and authorization server metadata using SDK helpers."""
    www_auth_resource_metadata_url: str | None = None
    try:
        probe = await httpx_client.get(server_url, timeout=5.0)
        if probe.status_code in (401, 403):
            www_auth_resource_metadata_url = extract_resource_metadata_from_www_auth(probe)
    except Exception:
        pass

    prm_urls = build_protected_resource_metadata_discovery_urls(www_auth_resource_metadata_url, server_url)
    prm: ProtectedResourceMetadata | None = None
    auth_server_url: str | None = None
    for url in prm_urls:
        req = create_oauth_metadata_request(url)
        try:
            resp = await httpx_client.send(req)
        except Exception:
            continue
        prm = await handle_protected_resource_response(resp)
        if prm is not None:
            if prm.authorization_servers:
                auth_server_url = str(prm.authorization_servers[0])
            break

    expected_issuer = auth_server_url or _origin_issuer(server_url)
    asm_urls = build_oauth_authorization_server_metadata_discovery_urls(auth_server_url, server_url)
    asm: OAuthMetadata | None = None
    for url in asm_urls:
        req = create_oauth_metadata_request(url)
        try:
            resp = await httpx_client.send(req)
        except Exception:
            continue
        ok, metadata = await handle_auth_metadata_response(resp)
        if not ok:
            break
        if ok and metadata is not None:
            if auth_server_url is None and issuers_match(str(metadata.issuer), expected_issuer):
                expected_issuer = str(metadata.issuer)
            try:
                validate_metadata_issuer(metadata, expected_issuer)
            except Exception:
                continue
            asm = metadata
            break

    return prm, asm


PrepareTokenAuth = Callable[[dict[str, str], dict[str, str] | None], tuple[dict[str, str], dict[str, str]]]


def single_client_auth(prepare: PrepareTokenAuth) -> PrepareTokenAuth:
    """Wrap OAuthContext.prepare_token_auth so a token request authenticates the client one way only.

    With HTTP Basic client auth the SDK still puts client_id in the form body. RFC 6749 (4.1.3, 6) needs it
    there only when the client does not authenticate, and strict servers (Sentry's) reject the request with
    "Client must not use multiple authentication methods". So with a Basic header, client_id leaves the body.
    """

    def prepare_token_auth(
        data: dict[str, str], headers: dict[str, str] | None = None
    ) -> tuple[dict[str, str], dict[str, str]]:
        data, headers = prepare(data, headers)
        if headers.get("Authorization", "").startswith("Basic "):
            data = {k: v for k, v in data.items() if k != "client_id"}
        return data, headers

    return prepare_token_auth


REFRESH_LOCK_TIMEOUT_S = 90.0
"""How long a refresh waits for another process's refresh (a token request itself times out well before)."""

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.asynccontextmanager
async def process_lock(path: Path, *, timeout_s: float = REFRESH_LOCK_TIMEOUT_S) -> AsyncIterator[None]:
    """An exclusive lock on ``path`` that holds across processes (and across holders inside one process).

    The operating system drops it when its holder dies, so a crashed refresh never wedges the account.
    Waiting polls, so a cancelled call gives up at once.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + timeout_s
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise McpFailure(ErrorKind.TIMEOUT, "another process held the token refresh for too long")
            await asyncio.sleep(0.05)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _register_token(token: OAuthToken) -> None:
    """Let ``redact`` mask the account's tokens (a tool that echoes its credentials must not leak them)."""
    register_secret(token.access_token)
    if token.refresh_token:
        register_secret(token.refresh_token)


class FarmOAuth(OAuth):
    """OAuth provider that persists metadata, refreshes via the server's real token endpoint,
    and classifies refresh failures into transient vs needs_login.

    A refresh is serialised across processes on ``refresh_lock`` (``farm run``, ``farm mcp sync`` and the
    command consumer share one token store), and what another process stored wins over a failed refresh.
    """

    def __init__(
        self,
        mcp_url: str | None = None,
        *args: Any,
        connection_id: str | None = None,
        refresh_lock: Path | None = None,
        **kwargs: Any,
    ) -> None:
        self._connection_id = connection_id or ""
        self._refresh_lock = refresh_lock
        super().__init__(mcp_url, *args, **kwargs)
        if (
            not self._connection_id
            and self._client_name.startswith("Harness Farm (")
            and self._client_name.endswith(")")
        ):
            self._connection_id = self._client_name[14:-1]

    def _bind(self, mcp_url: str) -> None:
        super()._bind(mcp_url)
        token_storage = self._token_storage or MemoryStore()
        adapter = FarmTokenStorageAdapter(
            async_key_value=token_storage,
            server_url=mcp_url,
        )
        self.token_storage_adapter = adapter
        self.context.storage = adapter
        self.storage = adapter
        self.context.prepare_token_auth = single_client_auth(self.context.prepare_token_auth)  # type: ignore[method-assign, assignment]

    async def _initialize(self) -> None:
        # Read the store under the refresh lock: on Windows a read can collide with another process replacing
        # the file it reads, and a refresh in progress is about to replace it.
        async with self._refresh_guard():
            await self._load_stored_state()

    async def _load_stored_state(self) -> None:
        await super()._initialize()
        if self.context.current_tokens is not None:
            _register_token(self.context.current_tokens)
        if self.context.oauth_metadata is None and isinstance(
            self.token_storage_adapter, FarmTokenStorageAdapter
        ):
            stored = await self.token_storage_adapter.get_metadata()
            if stored is not None:
                self.context.oauth_metadata = stored
        if self.context.protected_resource_metadata is None and isinstance(
            self.token_storage_adapter, FarmTokenStorageAdapter
        ):
            stored_prm = await self.token_storage_adapter.get_prm()
            if stored_prm is not None:
                self.context.protected_resource_metadata = stored_prm
                if stored_prm.authorization_servers:
                    self.context.auth_server_url = str(stored_prm.authorization_servers[0])

    async def discover_metadata(self) -> OAuthMetadata | None:
        """Discover authorization server metadata out-of-band via httpx_client_factory."""
        server_url = self.context.server_url or self.mcp_url
        async with self.httpx_client_factory() as client:
            prm, asm = await discover_metadata_from_server(server_url, client)
            if prm is not None:
                self.context.protected_resource_metadata = prm
                if prm.authorization_servers:
                    self.context.auth_server_url = str(prm.authorization_servers[0])
                if isinstance(self.token_storage_adapter, FarmTokenStorageAdapter):
                    await self.token_storage_adapter.set_prm(prm)
            if asm is not None:
                self.context.oauth_metadata = asm
                if isinstance(self.token_storage_adapter, FarmTokenStorageAdapter):
                    await self.token_storage_adapter.set_metadata(asm)
            return asm

    async def _handle_token_response(self, response: httpx2.Response) -> None:
        await super()._handle_token_response(response)
        if isinstance(self.token_storage_adapter, FarmTokenStorageAdapter):
            if self.context.oauth_metadata is not None:
                await self.token_storage_adapter.set_metadata(self.context.oauth_metadata)
            if self.context.protected_resource_metadata is not None:
                await self.token_storage_adapter.set_prm(self.context.protected_resource_metadata)

    async def _reload_stored_tokens(self) -> OAuthToken | None:
        """Make the stored tokens (maybe refreshed by another process) the current ones."""
        adapter = self.token_storage_adapter
        stored = await adapter.get_tokens()
        if stored is None:
            return None
        self.context.current_tokens = stored
        _register_token(stored)
        expiry = await adapter.get_token_expiry() if isinstance(adapter, TokenStorageAdapter) else None
        if expiry is not None:
            self.context.token_expiry_time = expiry
        else:
            self.context.update_token_expiry(stored)
        return stored

    def _refresh_guard(self) -> contextlib.AbstractAsyncContextManager[None]:
        if self._refresh_lock is None:
            return contextlib.nullcontext()
        return process_lock(self._refresh_lock)

    async def _handle_refresh_response(self, response: httpx2.Response) -> bool:
        token_url = self._get_token_endpoint()
        token_host = urlparse(token_url).netloc

        if response.status_code == 200:
            ok = await super()._handle_refresh_response(response)
            if ok:
                logger.info(
                    "mcp.oauth_refreshed",
                    connection=self._connection_id,
                    status=200,
                    token_host=token_host,
                )
            return ok

        content = await response.aread()
        body_text = content.decode("utf-8", errors="replace")

        is_invalid_grant = False
        if response.status_code in (400, 401):
            try:
                data = json.loads(body_text)
                if isinstance(data, dict) and data.get("error") == "invalid_grant":
                    is_invalid_grant = True
            except Exception:
                pass
            if not is_invalid_grant and "invalid_grant" in body_text:
                is_invalid_grant = True

        if is_invalid_grant:
            sent = self.context.current_tokens.refresh_token if self.context.current_tokens else None
            stored = await self.token_storage_adapter.get_tokens()
            if stored is not None and stored.refresh_token != sent:
                # Lost a race: another process refreshed first and stored its (rotated) tokens. Use them;
                # clearing the store would delete the winner's fresh tokens.
                await self._reload_stored_tokens()
                logger.info(
                    "mcp.oauth_refresh_lost_race", connection=self._connection_id, token_host=token_host
                )
                return self.context.is_token_valid()
            logger.warning(
                "mcp.oauth_refresh_failed",
                connection=self._connection_id,
                status=response.status_code,
                token_host=token_host,
                token_url=redact(token_url),
            )
            self.context.clear_tokens()
            await self.token_storage_adapter.clear()
            raise LoginRequired(
                f"OAuth refresh failed ({response.status_code}): invalid_grant; "
                "log in again with `farm mcp login`"
            )

        logger.warning(
            "mcp.oauth_refresh_failed",
            connection=self._connection_id,
            status=response.status_code,
            token_host=token_host,
            token_url=redact(token_url),
        )
        raise McpFailure(
            ErrorKind.SERVER,
            f"token refresh failed (HTTP {response.status_code}) at {redact(token_url)}",
        )

    async def _refresh_expired_token(self) -> None:
        """Refresh the access token. Under the cross-process lock the store is read first: a refresh another
        process has just made is used as it is, and only a token that is still expired is refreshed."""
        async with self._refresh_guard():
            if await self._reload_stored_tokens() is None:
                raise LoginRequired("this account has no usable OAuth token; log it in with `farm mcp login`")
            if self.context.is_token_valid():
                return
            if self.context.oauth_metadata is None:
                try:
                    asm = await self.discover_metadata()
                except (httpx2.TransportError, httpx2.TimeoutException, OSError) as exc:
                    server_url = self.context.server_url or self.mcp_url
                    token_host = urlparse(server_url).netloc
                    logger.warning(
                        "mcp.oauth_refresh_failed",
                        connection=self._connection_id,
                        status=None,
                        token_host=token_host,
                        token_url=redact(server_url),
                    )
                    raise McpFailure(
                        ErrorKind.SERVER,
                        f"OAuth metadata discovery failed (network error: {exc}) at {redact(server_url)}",
                    ) from exc

                if asm is None:
                    server_url = self.context.server_url or self.mcp_url
                    token_host = urlparse(server_url).netloc
                    logger.warning(
                        "mcp.oauth_refresh_failed",
                        connection=self._connection_id,
                        status=404,
                        token_host=token_host,
                        token_url=redact(server_url),
                    )
                    raise McpFailure(
                        ErrorKind.SERVER,
                        f"OAuth metadata discovery failed at {redact(server_url)}",
                    )

            refresh_request = await self._refresh_token()
            token_url = str(refresh_request.url)
            token_host = urlparse(token_url).netloc
            try:
                async with self.httpx_client_factory() as refresh_client:
                    refresh_response = await refresh_client.send(refresh_request)
            except (httpx2.TransportError, httpx2.TimeoutException, OSError) as exc:
                logger.warning(
                    "mcp.oauth_refresh_failed",
                    connection=self._connection_id,
                    status=None,
                    token_host=token_host,
                    token_url=redact(token_url),
                )
                raise McpFailure(
                    ErrorKind.SERVER,
                    f"token refresh failed (network error: {exc}) at {redact(token_url)}",
                ) from exc

            if not await self._handle_refresh_response(refresh_response):
                self._initialized = False

    async def _auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        async with self.context.lock:
            if not self._initialized:
                await self._initialize()
            delegate = not self.context.can_refresh_token()

        if delegate:
            # No refresh token yet (first login): the SDK flow takes self.context.lock itself and anyio locks
            # are not re-entrant, so it runs outside our hold of the lock.
            super_gen = super()._auth_flow(request)
            try:
                outgoing = await super_gen.__anext__()
                while True:
                    resp = yield outgoing
                    outgoing = await super_gen.asend(resp)
            except StopAsyncIteration:
                return
            return

        async with self.context.lock:
            self.context.protocol_version = request.headers.get(MCP_PROTOCOL_VERSION_HEADER)

            if not self.context.is_token_valid():
                await self._refresh_expired_token()

            if self.context.is_token_valid():
                self._add_auth_header(request)

            response = yield request

            step_up = (
                response.status_code == 403
                and extract_field_from_www_auth(response, "error") == "insufficient_scope"
            )

            if response.status_code == 401 or step_up:
                if isinstance(self, HeadlessOAuth):
                    raise LoginRequired(
                        "this account has no usable OAuth token; log it in with `farm mcp login`"
                    )
                super_gen = super()._auth_flow(request)
                try:
                    outgoing = await super_gen.__anext__()
                    while True:
                        resp = yield outgoing
                        outgoing = await super_gen.asend(resp)
                except StopAsyncIteration:
                    return

    async def redirect_handler(self, authorization_url: str) -> None:
        # A server has no browser: the URL is also printed, to be opened on any machine. With a fixed
        # FARM_OAUTH_CALLBACK_PORT and `ssh -L <port>:localhost:<port>` the redirect reaches this process.
        who = self._connection_id or "the account"
        print(f"Open this URL to log {who} in:\n  {authorization_url}", file=sys.stderr)
        await super().redirect_handler(authorization_url)


def oauth_callback_port() -> int | None:
    """``FARM_OAUTH_CALLBACK_PORT``: a fixed port for the login redirect (default: a free one per login)."""
    raw = os.environ.get("FARM_OAUTH_CALLBACK_PORT", "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise ValueError(f"FARM_OAUTH_CALLBACK_PORT must be a port number from 1024 to 65535, got {raw!r}")
    return int(raw)


class HeadlessOAuth(FarmOAuth):
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


CALL_META_KEY = "farm/call"
"""``_meta`` key under which a call carries its own token, to tell the answer to *that* request apart."""

SESSION_EVIDENCE_KEPT = 16


def _call_token(request: httpx2.Request) -> str | None:
    """The call token a JSON-RPC request carries in ``params._meta`` (none for ``initialize``, say)."""
    try:
        body = json.loads(request.content)
    except (httpx2.RequestNotRead, ValueError):
        return None
    messages = body if isinstance(body, list) else [body]
    for message in messages:
        params = message.get("params") if isinstance(message, dict) else None
        meta = params.get("_meta") if isinstance(params, dict) else None
        token = meta.get(CALL_META_KEY) if isinstance(meta, dict) else None
        if isinstance(token, str):
            return token
    return None


class CallTap:
    """The HTTP error evidence of one call. Concurrent calls on one connection never see each other's."""

    def __init__(self, tap: HttpStatusTap) -> None:
        self._tap = tap
        self.token = uuid.uuid4().hex
        self._started = time.monotonic()

    @property
    def meta(self) -> dict[str, str]:
        """Pass as the request's ``meta``: the answer to this request is then recorded under this call."""
        return {CALL_META_KEY: self.token}

    def failure(self) -> HttpFailure | None:
        """The error answer to this call's own request, else a session-level one (``initialize``, a listing)
        that arrived while the call was running. The latter is read, never taken: it belongs to every call
        on the session."""
        own = self._tap.calls.pop(self.token, None)
        if own is not None:
            return own
        for at, failure in reversed(self._tap.session):
            if at >= self._started:
                return failure
        return None

    def end(self) -> None:
        self._tap.calls.pop(self.token, None)


class HttpStatusTap:
    """Remembers the HTTP error answers to the JSON-RPC requests of an http / sse transport.

    The MCP SDK reduces every HTTP error answer to the same JSON-RPC error ("Server returned an error
    response"), so the status that tells the router what to do (401: log in again, 402: out of quota, 429:
    wait, 5xx: the server is failing) is read off the wire here, with an httpx response hook. A call that
    sends :attr:`CallTap.meta` gets the answer to its own request, whatever else runs on the connection.
    """

    def __init__(self) -> None:
        self.calls: dict[str, HttpFailure] = {}
        self.session: deque[tuple[float, HttpFailure]] = deque(maxlen=SESSION_EVIDENCE_KEPT)
        self._last: HttpFailure | None = None

    async def _record(self, response: httpx2.Response) -> None:
        if response.status_code < 400 or response.request.method != "POST":
            return
        failure = HttpFailure(response.status_code, retry_after_s(response))
        self._last = failure
        token = _call_token(response.request)
        if token is not None:
            self.calls[token] = failure
        else:
            self.session.append((time.monotonic(), failure))

    def begin(self) -> CallTap:
        """Start the evidence of one call (``end()`` it when the call is over)."""
        return CallTap(self)

    def take(self) -> HttpFailure | None:
        """The latest error answer of the connection, once (for a listing that is the only user)."""
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
    refresh_lock: Path | None = None,
    interactive: bool = False,
) -> ClientTransport:
    """The transport that reaches ``spec`` as ``connection``: provider block + the connection's overrides.

    ``interactive`` (``farm mcp login`` only) lets OAuth open the browser; a serving Farm never does.
    ``refresh_lock`` is the file that serialises token refreshes of this account across processes.
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
        log_dir = data_dir_path() / "logs" / "mcp"
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
        oauth_class = FarmOAuth if interactive else HeadlessOAuth
        auth = oauth_class(
            mcp_url=url,
            token_storage=token_storage(),
            client_name=f"Harness Farm ({connection.id})",
            connection_id=connection.id,
            refresh_lock=refresh_lock,
            callback_port=oauth_callback_port() if interactive else None,
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
