"""Browser authorization for one MCP connection, with a private token store until verified."""

from __future__ import annotations

import asyncio
import hmac
import os
from collections.abc import AsyncIterator, Mapping
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, parse_qsl, urlsplit
from uuid import UUID

import mcp_types
from mcp.shared.auth import AuthorizationCodeResult
from pydantic import AnyUrl
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from farm.connect.drivers.base import Failure, Identity, LoginEvent, NeedsInput, Success, Url
from farm.executors.base import ConnectionView
from farm.executors.mcp import relay
from farm.executors.mcp.client import McpExecutor, mcp_token_ref
from farm.executors.mcp.connect import FarmOAuth, build_client, build_transport, close_client
from farm.registry.models import McpProviderSpec
from farm.secrets import register_secret

CALLBACK_PATH = "/oauth/mcp/callback"
MAX_CALLBACK_LENGTH = 8192


def callback_url() -> str | None:
    """The exact externally registered URL, if configured by the operator."""
    raw = os.environ.get("FARM_MCP_OAUTH_CALLBACK_URL", "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        _ = parsed.port  # validate port syntax
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.path == CALLBACK_PATH
            and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and not any(char.isspace() for char in raw)
        ):
            return raw
    except ValueError:
        pass
    raise ValueError(f"FARM_MCP_OAUTH_CALLBACK_URL must be an HTTPS URL ending in {CALLBACK_PATH}")


def _query(query: str) -> dict[str, str]:
    if len(query) > MAX_CALLBACK_LENGTH:
        raise ValueError("The authorization response is too long.")
    pairs = parse_qsl(query, strict_parsing=True, keep_blank_values=True, max_num_fields=32)
    fields = dict(pairs)
    if len(pairs) != len(fields):
        raise ValueError("The authorization response has duplicate parameters.")
    return fields


def _redirect_target(url: SplitResult) -> tuple[str, str, int, str]:
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ValueError("The authorization redirect URL is invalid.")
    return url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80), url.path


class McpOAuthCallbacks:
    """Route a browser callback by the SDK's random, single-use state in this Farm process."""

    def __init__(self) -> None:
        self.pending: dict[str, McpOAuthDriver] = {}

    def wrap(self, app: ASGIApp) -> ASGIApp:
        async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http" or scope.get("path") != CALLBACK_PATH:
                await app(scope, receive, send)
                return
            ok = False
            if scope.get("method") == "GET":
                try:
                    fields = _query(scope.get("query_string", b"").decode("ascii"))
                    state = fields.get("state", "")
                    flow = self.pending.get(state)
                    if flow is not None:
                        flow.accept(fields)
                        ok = True
                except (UnicodeError, ValueError):
                    pass
            response = PlainTextResponse(
                "Response received. Return to the Farm Console for the sign-in result."
                if ok else "This authorization response could not be accepted. Return to the Farm Console.",
                status_code=200 if ok else 400,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
            await response(scope, receive, send)

        return dispatch


class _BrowserOAuth(FarmOAuth):
    """Use the installed SDK's PKCE exchange while the Console handles browser interaction."""

    def __init__(self, *args: Any, flow: McpOAuthDriver, **kwargs: Any) -> None:
        self.flow = flow
        super().__init__(*args, **kwargs)

    def _bind(self, mcp_url: str) -> None:
        super()._bind(mcp_url)
        if self.flow.public_callback is not None:
            redirect_uris = [AnyUrl(self.flow.public_callback)]
            self.context.client_metadata.redirect_uris = redirect_uris
            if self._static_client_info is not None:
                self._static_client_info.redirect_uris = redirect_uris
        self.flow.oauth = self

    async def redirect_handler(self, authorization_url: str) -> None:
        await self.flow.redirect(authorization_url)

    async def callback_handler(self) -> AuthorizationCodeResult:
        future = self.flow.response
        if future is None:
            raise ValueError("No authorization response is pending.")
        result = await future
        if result is None:
            raise ValueError("Authorization was declined by the provider.")
        return result


class McpOAuthDriver:
    method = "oauth_redirect"

    def __init__(
        self,
        executor: McpExecutor,
        connection: ConnectionView,
        spec: McpProviderSpec,
        callbacks: McpOAuthCallbacks,
    ) -> None:
        if spec.transport == "stdio" or spec.auth != "oauth" or not spec.url:
            raise ValueError("Browser sign-in requires an HTTP or SSE MCP connection configured for OAuth.")
        self.executor = executor
        self.connection = connection
        self.spec = spec
        self.callbacks = callbacks
        self.public_callback = callback_url()
        self.oauth: _BrowserOAuth | None = None
        self.response: asyncio.Future[AuthorizationCodeResult | None] | None = None
        self.state: str | None = None
        self.target: tuple[str, str, int, str] | None = None
        self.new_ref: str | None = None
        self.tools: list[mcp_types.Tool] = []
        self._events: asyncio.Queue[LoginEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._declined = False
        self._received = False

    def staging_dir(self, tenant: UUID, session: UUID) -> Path:
        self.new_ref = mcp_token_ref(tenant, self.connection.id, session)
        staging = self.connection.model_copy(update={"auth_ref": self.new_ref})
        return self.executor.get_token_store_dir(staging)

    async def start(self, home: Path, hint: str | None = None) -> None:
        self._task = asyncio.create_task(self._authenticate(), name="mcp-browser-auth")

    async def _authenticate(self) -> None:
        assert self.new_ref is not None
        connection = self.connection.model_copy(update={"auth_ref": self.new_ref})
        try:
            transport = build_transport(
                connection,
                self.spec,
                token_storage=lambda: self.executor.token_storage(connection),
                oauth_factory=partial(_BrowserOAuth, flow=self),
            )
            client = build_client(transport, connection.id)
            try:
                self.tools = await relay.list_tools(client, timeout_s=620)
                oauth = self.oauth
                has_token = oauth is not None and await oauth.token_storage_adapter.get_tokens() is not None
                if not self._received or not has_token:
                    raise ValueError("The server did not complete OAuth.")
            finally:
                await close_client(client)
        except Exception:
            # Provider/SDK errors can contain the OAuth response or an upstream request body.
            message = (
                "The provider declined authorization. Start again when you are ready."
                if self._declined else
                "MCP sign-in did not complete. Check OAuth registration, callback and permissions."
            )
            self._events.put_nowait(Failure("mcp_oauth_failed", message))
        else:
            self._events.put_nowait(Success())

    async def redirect(self, url: str) -> None:
        parsed = urlsplit(url)
        # Public authorization servers must use HTTPS. Loopback HTTP permits isolated local OAuth.
        scheme, host, _, _ = _redirect_target(parsed)
        if parsed.fragment or (scheme != "https" and host not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("The provider's authorization endpoint is invalid.")
        fields = _query(parsed.query)
        state = fields.get("state")
        if not state:
            raise ValueError("The provider's authorization state is missing.")
        self._forget_state()
        self.state = state
        self.target = _redirect_target(urlsplit(fields["redirect_uri"]))
        self.response = asyncio.get_running_loop().create_future()
        if self.public_callback is not None:
            self.callbacks.pending[state] = self
        self._events.put_nowait(Url(url))
        if self.public_callback is None:
            self._events.put_nowait(NeedsInput("Paste the full address from the browser after approval."))

    def accept(self, fields: Mapping[str, str]) -> None:
        state = fields.get("state", "")
        future = self.response
        if future is None or future.done() or not self.state or not hmac.compare_digest(
            state.encode("utf-8"), self.state.encode("utf-8")
        ):
            raise ValueError("This authorization response does not match the waiting sign-in.")
        code = fields.get("code", "")
        error = fields.get("error", "")
        if bool(code) == bool(error):
            raise ValueError("The authorization response is incomplete.")
        if error:
            self._declined = True
            future.set_result(None)
        else:
            register_secret(code)
            self._received = True
            future.set_result(AuthorizationCodeResult(code=code, state=state, iss=fields.get("iss")))
        self._forget_state()

    def _forget_state(self) -> None:
        if self.state is not None and self.callbacks.pending.get(self.state) is self:
            del self.callbacks.pending[self.state]
        self.state = None

    async def events(self) -> AsyncIterator[LoginEvent]:
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, (Success, Failure)):
                return

    async def submit(self, text: str) -> None:
        invalid_input = len(text) > MAX_CALLBACK_LENGTH or any(c.isspace() for c in text)
        if self.public_callback is not None or invalid_input:
            raise ValueError("Paste the full authorization redirect URL.")
        parsed = urlsplit(text)
        if parsed.fragment or _redirect_target(parsed) != self.target:
            raise ValueError("The pasted address does not match this sign-in's redirect URL.")
        self.accept(_query(parsed.query))

    async def cancel(self) -> None:
        self._forget_state()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.response is not None and not self.response.done():
            self.response.cancel()

    async def identity(self, home: Path) -> Identity | None:
        return None  # MCP does not define an identity endpoint.

    async def models(self, home: Path) -> list[str]:
        return []
