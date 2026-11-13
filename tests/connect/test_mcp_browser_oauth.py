"""Exercise the installed OAuth and MCP clients against a fake provider, without networking."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit
from uuid import UUID

import httpx2
import pytest
from fastmcp.client.transports import StreamableHttpTransport
from psycopg.types.json import Jsonb
from starlette.responses import PlainTextResponse

from farm.connect import sessions
from farm.connect.drivers import mcp_oauth
from farm.connect.drivers.mcp_oauth import CALLBACK_PATH, McpOAuthCallbacks
from farm.connect.sealed import seal
from farm.connect.service import CheckResult, LoginError, LoginService
from farm.db.pool import DbPool, tenant_connection
from farm.executors.base import ConnectionView
from farm.executors.mcp.client import McpExecutor, mcp_token_ref
from farm.executors.mcp.connect import FarmTokenStorageAdapter
from farm.mcp.store import DbDirectory, load_tools
from farm.registry.models import McpProviderSpec
from farm.tenancy import MILVOW_TENANT_ID, use_tenant

MCP_URL = "https://resource.example.test/mcp"
ISSUER = "https://oauth.example.test"
PUBLIC_CALLBACK = f"https://farm.example.test{CALLBACK_PATH}"
CONNECTION = "test-browser-oauth-01"
PROVIDER = "test-browser-oauth"
LEGACY_REF = "token-store:legacy-test-browser-oauth"
ACCESS = "test-access-token-from-staged-login"
CODE = "test-code-from-browser"


class Provider:
    def __init__(self) -> None:
        self.redirect: str | None = None
        self.challenge: str | None = None
        self.exchanges = 0
        self.handshakes = 0
        self.fail_listing = False
        self.anonymous = False

    def client(self, **kwargs: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.request), **kwargs)

    async def request(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/.well-known/oauth-protected-resource/mcp":
            return httpx2.Response(200, json={"resource": MCP_URL, "authorization_servers": [ISSUER]})
        if path == "/.well-known/oauth-authorization-server":
            return httpx2.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "response_types_supported": ["code"],
                "code_challenge_methods_supported": ["S256"],
                "authorization_response_iss_parameter_supported": True,
            })
        if path == "/token":
            self.exchanges += 1
            form = dict(parse_qsl(request.content.decode()))
            assert form["code"] == CODE
            assert form["redirect_uri"] == self.redirect
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == self.challenge
            return httpx2.Response(200, json={"access_token": ACCESS, "token_type": "Bearer", "expires_in": 3600})
        if path != "/mcp":
            return httpx2.Response(404)
        if not self.anonymous and request.headers.get("authorization") != f"Bearer {ACCESS}":
            return httpx2.Response(401, headers={
                "WWW-Authenticate": 'Bearer resource_metadata="https://resource.example.test/'
                '.well-known/oauth-protected-resource/mcp"',
            })
        if request.method != "POST":
            return httpx2.Response(405)
        rpc = json.loads(request.content)
        if "id" not in rpc:
            return httpx2.Response(202)
        if rpc["method"] == "server/discover":
            return httpx2.Response(200, json={
                "jsonrpc": "2.0", "id": rpc["id"],
                "error": {"code": -32601, "message": "Method not found"},
            })
        if rpc["method"] == "initialize":
            self.handshakes += 1
            result = {
                "protocolVersion": rpc["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"},
            }
        elif rpc["method"] == "tools/list":
            if self.fail_listing:
                return httpx2.Response(502)
            result = {"tools": [{"name": "example", "inputSchema": {"type": "object"}}]}
        else:
            raise AssertionError(f"Unexpected MCP method {rpc['method']}")
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result})

    def redirect_for(self, auth_url: str, *, state: str | None = None, issuer: str = ISSUER) -> str:
        parts = urlsplit(auth_url)
        assert parts.netloc == "oauth.example.test"
        fields = dict(parse_qsl(parts.query))
        assert fields["code_challenge_method"] == "S256"
        self.challenge = fields["code_challenge"]
        self.redirect = fields["redirect_uri"]
        return f"{self.redirect}?{urlencode({'code': CODE, 'state': state or fields['state'], 'iss': issuer})}"


async def _seed(pool: DbPool, auth_ref: str = LEGACY_REF) -> None:
    spec = McpProviderSpec(transport="http", url=MCP_URL, auth="oauth", oauth_client_id="test-client")
    async with tenant_connection(pool) as conn:
        await conn.execute(
            "insert into public.providers (id, name, kind, executor, config) values (%s,%s,'tool','mcp',%s)",
            (PROVIDER, "Browser OAuth", Jsonb({"mcp": spec.model_dump(mode="json")})),
        )
        await conn.execute(
            "insert into public.connections (id, provider_id, label, auth_ref, status, meta) "
            "values (%s,%s,%s,%s,'needs_login','{}')",
            (CONNECTION, PROVIDER, "Browser OAuth Account", auth_ref),
        )


async def _no_test(_: str, __: Path) -> CheckResult:
    raise AssertionError("MCP OAuth must not invoke an AI test")


def _no_cli(*_: Any) -> Any:
    raise AssertionError("MCP OAuth must not start an AI CLI")


def _setup(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, public: bool, timeout: float = 3,
) -> tuple[LoginService, McpExecutor, McpOAuthCallbacks, Provider]:
    if public:
        monkeypatch.setenv("FARM_MCP_OAUTH_CALLBACK_URL", PUBLIC_CALLBACK)
    else:
        monkeypatch.delenv("FARM_MCP_OAUTH_CALLBACK_URL", raising=False)
    executor = McpExecutor(data_dir=tmp_path, directory=DbDirectory(pool))
    callbacks = McpOAuthCallbacks()
    service = LoginService(
        pool, driver_factory=_no_cli, test_job=_no_test, keys_dir=tmp_path / "keys",
        mcp_executor=executor, mcp_callbacks=callbacks, user_timeout_s=timeout,
    )
    provider = Provider()
    original = mcp_oauth.build_transport

    def build(*args: Any, **kwargs: Any) -> StreamableHttpTransport:
        transport = original(*args, **kwargs)
        assert isinstance(transport, StreamableHttpTransport)
        transport.httpx_client_factory = provider.client
        return transport

    monkeypatch.setattr(mcp_oauth, "build_transport", build)
    return service, executor, callbacks, provider


async def _wait(pool: DbPool, session_id: UUID, state: str, *, input_ready: bool = False) -> sessions.Session:
    async with asyncio.timeout(5):
        while True:
            row = await sessions.get(pool, session_id)
            assert row is not None
            if row["state"] == state and (not input_ready or row["needs_input"]):
                return row
            if row["finished_at"] is not None:
                pytest.fail(f"Unexpected session result {row['state']}: {row['error_excerpt']}")
            await asyncio.sleep(0.02)


async def _connection(pool: DbPool) -> tuple[str, str]:
    async with tenant_connection(pool) as conn:
        cur = await conn.execute("select auth_ref, status from public.connections where id=%s", (CONNECTION,))
        row = await cur.fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def _sealed(service: LoginService, session: UUID, url: str) -> dict[str, Any]:
    return seal(url, service._public_jwk(), f"{MILVOW_TENANT_ID}|{session}|login_input")


async def _browser(callbacks: McpOAuthCallbacks, url: str) -> httpx2.Response:
    async def unavailable(scope: Any, receive: Any, send: Any) -> None:
        await PlainTextResponse("Bearer required", status_code=401)(scope, receive, send)

    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=callbacks.wrap(unavailable))) as client:
        return await client.get(url)


@pytest.mark.parametrize("public", [True, False])
async def test_oauth_callback_promotes_only_after_mcp_handshake(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, public: bool,
) -> None:
    await _seed(pool)
    service, executor, callbacks, provider = _setup(pool, tmp_path, monkeypatch, public=public)
    monkeypatch.setenv("FARM_SHARED_INSTANCE", "1")
    try:
        session = await service.start_login(MILVOW_TENANT_ID, CONNECTION, "owner")
        assert await service.start_login(MILVOW_TENANT_ID, CONNECTION, "owner") == session
        row = await _wait(pool, session, "waiting_for_user", input_ready=not public)
        assert await _connection(pool) == (LEGACY_REF, "needs_login")
        assert await load_tools(pool, [PROVIDER]) == []
        redirect = provider.redirect_for(row["auth_url"])
        if public:
            assert provider.redirect == PUBLIC_CALLBACK
            assert (await _browser(callbacks, provider.redirect_for(row["auth_url"], state="wrong"))).status_code == 400
            response = await _browser(callbacks, redirect)
            assert response.status_code == 200 and CODE not in response.text
            assert response.headers["cache-control"] == "no-store"
            assert (await _browser(callbacks, redirect)).status_code == 400
        else:
            assert provider.redirect and provider.redirect.startswith("http://localhost:")
            with pytest.raises(LoginError, match="complete redirect URL"):
                await service.submit_input(MILVOW_TENANT_ID, session, _sealed(service, session, CODE), "owner")
            with pytest.raises(LoginError, match="complete redirect URL"):
                wrong = redirect.replace("localhost", "malicious.example.test")
                await service.submit_input(MILVOW_TENANT_ID, session, _sealed(service, session, wrong), "owner")
            await service.submit_input(MILVOW_TENANT_ID, session, _sealed(service, session, redirect), "owner")
        done = await _wait(pool, session, "done")
        active_ref, status = await _connection(pool)
        assert active_ref == mcp_token_ref(MILVOW_TENANT_ID, CONNECTION, session)
        assert status == "active"
        assert not done["identity"] and not done["auth_url"]
        assert done["evidence"] == {"tools_count": 1, "auth_verified": True, "identity_verified": False}
        assert provider.exchanges == 1 and provider.handshakes == 1
        assert [tool.name for tool in await load_tools(pool, [PROVIDER])] == ["example"]
        conn = ConnectionView(id=CONNECTION, provider_id=PROVIDER, auth_ref=active_ref)
        stored = await FarmTokenStorageAdapter(executor.token_storage(conn), MCP_URL).get_tokens()
        assert stored is not None and stored.access_token == ACCESS
    finally:
        await service.stop()
        await executor.aclose()


@pytest.mark.parametrize("outcome", ["cancelled", "expired", "wrong_issuer", "failed_list", "anonymous"])
async def test_oauth_failure_preserves_legacy_credentials(
    pool: DbPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    await _seed(pool)
    service, executor, callbacks, provider = _setup(
        pool, tmp_path, monkeypatch, public=True, timeout=0.6 if outcome == "expired" else 3,
    )
    async with tenant_connection(pool) as conn:
        await conn.execute("update public.connections set status='active' where id=%s", (CONNECTION,))
    legacy = ConnectionView(id=CONNECTION, provider_id=PROVIDER, auth_ref=LEGACY_REF)
    sentinel = executor.get_token_store_dir(legacy) / "previous"
    sentinel.write_text("previous fake credentials", encoding="utf-8")
    provider.fail_listing = outcome == "failed_list"
    provider.anonymous = outcome == "anonymous"
    try:
        session = await service.start_login(MILVOW_TENANT_ID, CONNECTION, "owner")
        if outcome == "anonymous":
            await _wait(pool, session, "failed")
        else:
            waiting = await _wait(pool, session, "waiting_for_user")
            redirect = provider.redirect_for(
                waiting["auth_url"], issuer="https://unexpected.example.test" if outcome == "wrong_issuer" else ISSUER,
            )
            if outcome == "cancelled":
                assert await service.cancel(MILVOW_TENANT_ID, session, "owner") == "cancelled"
            elif outcome == "expired":
                await _wait(pool, session, "expired")
            else:
                assert (await _browser(callbacks, redirect)).status_code == 200
                await _wait(pool, session, "failed")
            assert (await _browser(callbacks, redirect)).status_code == 400
        assert await _connection(pool) == (LEGACY_REF, "active")
        assert sentinel.read_text(encoding="utf-8") == "previous fake credentials"
        assert not (tmp_path / "tokens" / mcp_token_ref(MILVOW_TENANT_ID, CONNECTION, session)[12:]).exists()
        if outcome != "failed_list":
            assert provider.exchanges == 0
    finally:
        await service.stop()
        await executor.aclose()


async def test_same_connection_id_cannot_open_another_tenants_store(tmp_path: Path) -> None:
    executor = McpExecutor(data_dir=tmp_path)
    tenant_b = UUID("32a231e7-a496-442a-9e15-cdf884a6daba")
    a_ref = mcp_token_ref(MILVOW_TENANT_ID, CONNECTION)
    b_ref = mcp_token_ref(tenant_b, CONNECTION)
    a = ConnectionView(id=CONNECTION, provider_id=PROVIDER, auth_ref=a_ref)
    with use_tenant(tenant_b):
        with pytest.raises(ValueError, match="different tenant"):
            executor.get_token_store_dir(a)
        b_dir = executor.get_token_store_dir(a.model_copy(update={"auth_ref": b_ref}))
    assert executor.get_token_store_dir(a) != b_dir
