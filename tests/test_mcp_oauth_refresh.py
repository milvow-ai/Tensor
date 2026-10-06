"""Acceptance tests for MCP OAuth token refresh (OAUTH1).

Tests:
1. Login stores tokens and metadata; a new client instance (simulating a restart) with an expired access
   token refreshes via the real token endpoint (/oauth/token) and the call succeeds.
2. When tokens exist in storage but no oauth_metadata is set, metadata is discovered first, persisted,
   and used for refresh.
3. Refresh failure with 404/5xx/network becomes transient (ErrorKind.SERVER / cooldown + retry), never
   needs_login, tokens are preserved in storage, and the log names the token URL.
4. Refresh failure with 400/401 invalid_grant becomes needs_login (ErrorKind.NEEDS_LOGIN) and tokens
   are cleared from storage.
5. Refresh outcome logging (mcp.oauth_refreshed / mcp.oauth_refresh_failed) records status and token host.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from structlog.testing import capture_logs

from farm.executors.base import ConnectionView, ErrorKind
from farm.executors.mcp import relay
from farm.executors.mcp.connect import (
    FarmOAuth,
    FarmTokenStorageAdapter,
    HeadlessOAuth,
    build_transport,
)
from farm.registry.models import McpProviderSpec


def _make_server_and_routes(
    token_mode: str = "success",
    token_hits: list[str] | None = None,
    hits_list: list[str] | None = None,
) -> Any:
    server = FastMCP("test-oauth-server")

    @server.tool(name="echo")
    def echo(message: str = "hello") -> str:
        return f"echo: {message}"

    async def auth_server_metadata(request: Any) -> Response:
        if hits_list is not None:
            hits_list.append(str(request.url))
        return JSONResponse({
            "issuer": "http://127.0.0.1",
            "authorization_endpoint": "http://127.0.0.1/oauth/authorize",
            "token_endpoint": "http://127.0.0.1/oauth/token",
            "response_types_supported": ["code"],
        })

    async def token_endpoint(request: Any) -> Response:
        url_str = str(request.url)
        if token_hits is not None:
            token_hits.append(url_str)
        if hits_list is not None:
            hits_list.append(url_str)
        form = await request.form()
        grant_type = form.get("grant_type")
        if grant_type == "refresh_token":
            if token_mode == "success":
                return JSONResponse({
                    "access_token": "fresh-access-token-456",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt-123",
                })
            elif token_mode == "404":
                return Response(status_code=404)
            elif token_mode == "503":
                return Response(status_code=503)
            elif token_mode == "invalid_grant":
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            elif token_mode == "invalid_grant_401":
                return JSONResponse({"error": "invalid_grant"}, status_code=401)
        elif grant_type == "authorization_code":
            return JSONResponse({
                "access_token": "initial-access-token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "rt-123",
            })
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    async def default_token_404(request: Any) -> Response:
        url_str = str(request.url)
        if token_hits is not None:
            token_hits.append(url_str)
        if hits_list is not None:
            hits_list.append(url_str)
        return Response(status_code=404)

    app = server.http_app()
    app.routes.append(Route("/.well-known/oauth-authorization-server", auth_server_metadata))
    app.routes.append(Route("/oauth/token", token_endpoint, methods=["POST"]))
    app.routes.append(Route("/token", default_token_404, methods=["POST"]))
    return server, app


def _store(tmp_path: Path) -> FileTreeStore:
    return FileTreeStore(
        data_directory=str(tmp_path),
        key_sanitization_strategy=AlwaysHashStrategy(),
        collection_sanitization_strategy=AlwaysHashStrategy(),
    )


@pytest.mark.asyncio
async def test_oauth_login_stores_tokens_and_restart_refreshes_via_non_default_token_path(
    tmp_path: Path,
) -> None:
    token_hits: list[str] = []
    _server, app = _make_server_and_routes(token_mode="success", token_hits=token_hits)

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **_: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    store = _store(tmp_path)
    adapter = FarmTokenStorageAdapter(async_key_value=store, server_url="http://127.0.0.1/mcp")

    # Step 1: Simulate tokens stored at login (with metadata persisted alongside)
    token = OAuthToken(
        access_token="expired-token-123",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="rt-123",
        scope="mcp",
    )
    client_info = OAuthClientInformationFull(client_id="clay-client-id", client_secret="clay-secret")
    metadata = OAuthMetadata(
        issuer="http://127.0.0.1",
        authorization_endpoint="http://127.0.0.1/oauth/authorize",
        token_endpoint="http://127.0.0.1/oauth/token",
        response_types_supported=["code"],
    )

    await adapter.set_tokens(token)
    await adapter.set_client_info(client_info)
    await adapter.set_metadata(metadata)
    # Set stored expiry in the past so token is expired
    await store.put(
        key="http://127.0.0.1/mcp/token_expiry",
        value={"expires_at": time.time() - 60},
        collection="mcp-oauth-token-expiry",
    )

    # Step 2: New client instance simulating restart with HeadlessOAuth
    auth = HeadlessOAuth(
        mcp_url="http://127.0.0.1/mcp",
        token_storage=store,
        client_name="Harness Farm (clay-01)",
        connection_id="clay-01",
        httpx_client_factory=factory,
    )

    transport = StreamableHttpTransport(
        "http://127.0.0.1/mcp", auth=auth, httpx_client_factory=factory
    )
    client = Client(transport)

    with capture_logs() as captured:
        async with app.router.lifespan_context(app):
            async with client:
                res = await client.call_tool("echo", {"message": "hello restart"})

    assert not res.is_error
    assert res.content[0].text == "echo: hello restart"  # type: ignore[union-attr]

    # Verified: token refresh hit /oauth/token, never /token
    assert any("/oauth/token" in h for h in token_hits)
    assert "http://127.0.0.1/token" not in token_hits

    # Verified log: mcp.oauth_refreshed recorded status 200 and token host
    refreshed_events = [e for e in captured if e.get("event") == "mcp.oauth_refreshed"]
    assert len(refreshed_events) == 1
    assert refreshed_events[0]["status"] == 200
    assert refreshed_events[0]["token_host"] == "127.0.0.1"
    assert refreshed_events[0]["connection"] == "clay-01"


@pytest.mark.asyncio
async def test_oauth_restart_discovers_metadata_when_missing_from_store_and_refreshes(
    tmp_path: Path,
) -> None:
    token_hits: list[str] = []
    hits_list: list[str] = []
    _server, app = _make_server_and_routes(
        token_mode="success", token_hits=token_hits, hits_list=hits_list
    )

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **_: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    store = _store(tmp_path)
    adapter = FarmTokenStorageAdapter(async_key_value=store, server_url="http://127.0.0.1/mcp")

    # Tokens exist from store, but NO oauth_metadata is set in store
    token = OAuthToken(
        access_token="expired-token-123",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="rt-123",
        scope="mcp",
    )
    client_info = OAuthClientInformationFull(client_id="clay-client-id")
    await adapter.set_tokens(token)
    await adapter.set_client_info(client_info)
    await store.put(
        key="http://127.0.0.1/mcp/token_expiry",
        value={"expires_at": time.time() - 60},
        collection="mcp-oauth-token-expiry",
    )

    assert await adapter.get_metadata() is None

    auth = HeadlessOAuth(
        mcp_url="http://127.0.0.1/mcp",
        token_storage=store,
        client_name="Harness Farm (clay-02)",
        connection_id="clay-02",
        httpx_client_factory=factory,
    )

    transport = StreamableHttpTransport(
        "http://127.0.0.1/mcp", auth=auth, httpx_client_factory=factory
    )
    client = Client(transport)

    with capture_logs() as captured:
        async with app.router.lifespan_context(app):
            async with client:
                res = await client.call_tool("echo", {"message": "discovered"})

    assert not res.is_error
    assert res.content[0].text == "echo: discovered"  # type: ignore[union-attr]

    # Discovery occurred first, then /oauth/token refresh
    assert any("/.well-known/oauth-authorization-server" in h for h in hits_list)
    assert any("/oauth/token" in h for h in token_hits)
    assert "http://127.0.0.1/token" not in token_hits

    # Discovered metadata is now persisted in storage
    stored_meta = await adapter.get_metadata()
    assert stored_meta is not None
    assert str(stored_meta.token_endpoint) == "http://127.0.0.1/oauth/token"

    # Log recorded
    refreshed_events = [e for e in captured if e.get("event") == "mcp.oauth_refreshed"]
    assert len(refreshed_events) == 1
    assert refreshed_events[0]["status"] == 200


@pytest.mark.asyncio
async def test_oauth_refresh_404_becomes_transient_not_needs_login(tmp_path: Path) -> None:
    token_hits: list[str] = []
    _server, app = _make_server_and_routes(token_mode="404", token_hits=token_hits)

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **_: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    store = _store(tmp_path)
    adapter = FarmTokenStorageAdapter(async_key_value=store, server_url="http://127.0.0.1/mcp")

    token = OAuthToken(
        access_token="expired-token",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="rt-123",
        scope="mcp",
    )
    client_info = OAuthClientInformationFull(client_id="clay-client-id")
    await adapter.set_tokens(token)
    await adapter.set_client_info(client_info)
    await store.put(
        key="http://127.0.0.1/mcp/token_expiry",
        value={"expires_at": time.time() - 60},
        collection="mcp-oauth-token-expiry",
    )

    auth = HeadlessOAuth(
        mcp_url="http://127.0.0.1/mcp",
        token_storage=store,
        client_name="Harness Farm (clay-01)",
        connection_id="clay-01",
        httpx_client_factory=factory,
    )

    transport = StreamableHttpTransport(
        "http://127.0.0.1/mcp", auth=auth, httpx_client_factory=factory
    )
    client = Client(transport)

    with capture_logs() as captured:
        async with app.router.lifespan_context(app):
            with pytest.raises(Exception) as caught:
                async with client:
                    await client.call_tool("echo", {})

    failure = relay.classify(caught.value, oauth=True)

    # Must be transient (SERVER), NEVER needs_login
    assert failure.kind is ErrorKind.SERVER
    assert failure.kind is not ErrorKind.NEEDS_LOGIN

    # Tokens must NOT be cleared from storage
    stored_token = await adapter.get_tokens()
    assert stored_token is not None
    assert stored_token.refresh_token == "rt-123"

    # Log names token URL (no secrets) and token host
    failed_events = [e for e in captured if e.get("event") == "mcp.oauth_refresh_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["status"] == 404
    assert failed_events[0]["token_host"] == "127.0.0.1"
    assert "http://127.0.0.1/oauth/token" in failed_events[0]["token_url"]


@pytest.mark.asyncio
async def test_oauth_refresh_invalid_grant_becomes_needs_login(tmp_path: Path) -> None:
    _server, app = _make_server_and_routes(token_mode="invalid_grant")

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **_: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    store = _store(tmp_path)
    adapter = FarmTokenStorageAdapter(async_key_value=store, server_url="http://127.0.0.1/mcp")

    token = OAuthToken(
        access_token="expired-token",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="rt-123",
        scope="mcp",
    )
    client_info = OAuthClientInformationFull(client_id="clay-client-id")
    await adapter.set_tokens(token)
    await adapter.set_client_info(client_info)
    await store.put(
        key="http://127.0.0.1/mcp/token_expiry",
        value={"expires_at": time.time() - 60},
        collection="mcp-oauth-token-expiry",
    )

    auth = HeadlessOAuth(
        mcp_url="http://127.0.0.1/mcp",
        token_storage=store,
        client_name="Harness Farm (clay-01)",
        connection_id="clay-01",
        httpx_client_factory=factory,
    )

    transport = StreamableHttpTransport(
        "http://127.0.0.1/mcp", auth=auth, httpx_client_factory=factory
    )
    client = Client(transport)

    with capture_logs() as captured:
        async with app.router.lifespan_context(app):
            with pytest.raises(Exception) as caught:
                async with client:
                    await client.call_tool("echo", {})

    failure = relay.classify(caught.value, oauth=True)

    # Must be NEEDS_LOGIN
    assert failure.kind is ErrorKind.NEEDS_LOGIN

    # Tokens MUST be cleared from storage
    stored_token = await adapter.get_tokens()
    assert stored_token is None

    # Log names token URL and status 400
    failed_events = [e for e in captured if e.get("event") == "mcp.oauth_refresh_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["status"] == 400
    assert failed_events[0]["token_host"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_oauth_refresh_5xx_becomes_transient_not_needs_login(tmp_path: Path) -> None:
    _server, app = _make_server_and_routes(token_mode="503")

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **_: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    store = _store(tmp_path)
    adapter = FarmTokenStorageAdapter(async_key_value=store, server_url="http://127.0.0.1/mcp")

    token = OAuthToken(
        access_token="expired-token",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="rt-123",
        scope="mcp",
    )
    client_info = OAuthClientInformationFull(client_id="clay-client-id")
    await adapter.set_tokens(token)
    await adapter.set_client_info(client_info)
    await store.put(
        key="http://127.0.0.1/mcp/token_expiry",
        value={"expires_at": time.time() - 60},
        collection="mcp-oauth-token-expiry",
    )

    auth = HeadlessOAuth(
        mcp_url="http://127.0.0.1/mcp",
        token_storage=store,
        client_name="Harness Farm (clay-01)",
        connection_id="clay-01",
        httpx_client_factory=factory,
    )

    transport = StreamableHttpTransport(
        "http://127.0.0.1/mcp", auth=auth, httpx_client_factory=factory
    )
    client = Client(transport)

    with capture_logs() as captured:
        async with app.router.lifespan_context(app):
            with pytest.raises(Exception) as caught:
                async with client:
                    await client.call_tool("echo", {})

    failure = relay.classify(caught.value, oauth=True)
    assert failure.kind is ErrorKind.SERVER
    assert failure.kind is not ErrorKind.NEEDS_LOGIN

    # Tokens preserved
    assert await adapter.get_tokens() is not None

    failed_events = [e for e in captured if e.get("event") == "mcp.oauth_refresh_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["status"] == 503


@pytest.mark.asyncio
async def test_oauth_refresh_network_error_becomes_transient_not_needs_login(tmp_path: Path) -> None:
    _server, app = _make_server_and_routes(token_mode="success")

    class NetworkFailingTransport(httpx2.AsyncBaseTransport):
        def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
            self._inner = inner

        async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
            if request.url.path == "/oauth/token":
                raise httpx2.ConnectError("connection refused", request=request)
            return await self._inner.handle_async_request(request)

    def factory(headers: Any = None, timeout: Any = None, auth: Any = None, **_: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=NetworkFailingTransport(httpx2.ASGITransport(app=app)),
            headers=headers,
            timeout=timeout,
            auth=auth,
        )

    store = _store(tmp_path)
    adapter = FarmTokenStorageAdapter(async_key_value=store, server_url="http://127.0.0.1/mcp")

    token = OAuthToken(
        access_token="expired-token",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="rt-123",
        scope="mcp",
    )
    client_info = OAuthClientInformationFull(client_id="clay-client-id")
    await adapter.set_tokens(token)
    await adapter.set_client_info(client_info)
    await store.put(
        key="http://127.0.0.1/mcp/token_expiry",
        value={"expires_at": time.time() - 60},
        collection="mcp-oauth-token-expiry",
    )

    auth = HeadlessOAuth(
        mcp_url="http://127.0.0.1/mcp",
        token_storage=store,
        client_name="Harness Farm (clay-01)",
        connection_id="clay-01",
        httpx_client_factory=factory,
    )

    transport = StreamableHttpTransport(
        "http://127.0.0.1/mcp", auth=auth, httpx_client_factory=factory
    )
    client = Client(transport)

    with capture_logs() as captured:
        async with app.router.lifespan_context(app):
            with pytest.raises(Exception) as caught:
                async with client:
                    await client.call_tool("echo", {})

    failure = relay.classify(caught.value, oauth=True)
    assert failure.kind is ErrorKind.SERVER
    assert failure.kind is not ErrorKind.NEEDS_LOGIN

    assert await adapter.get_tokens() is not None

    failed_events = [e for e in captured if e.get("event") == "mcp.oauth_refresh_failed"]
    assert len(failed_events) == 1
    assert failed_events[0]["status"] is None
    assert failed_events[0]["token_host"] == "127.0.0.1"


def test_build_transport_oauth_wiring(tmp_path: Path) -> None:
    spec = McpProviderSpec(
        transport="http",
        url="http://127.0.0.1:8000/mcp",
        auth="oauth",
    )
    conn = ConnectionView(
        id="conn-10",
        provider_id="clay",
        auth_ref="token-store:conn-10",
    )

    def store_fn() -> FileTreeStore:
        return FileTreeStore(data_directory=str(tmp_path))

    serving = build_transport(conn, spec, token_storage=store_fn)
    login = build_transport(conn, spec, token_storage=store_fn, interactive=True)

    assert isinstance(serving, StreamableHttpTransport)
    assert isinstance(serving.auth, HeadlessOAuth)
    assert serving.auth._connection_id == "conn-10"

    assert isinstance(login, StreamableHttpTransport)
    assert isinstance(login.auth, FarmOAuth)
    assert not isinstance(login.auth, HeadlessOAuth)
    assert login.auth._connection_id == "conn-10"
