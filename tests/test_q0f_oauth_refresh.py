"""Q0-11: OAuth refresh is serialised across processes, and a lost race re-reads the winner's token.

``farm run``, ``farm mcp sync`` / ``login``, doctor and the command consumer each build their own executor over
the same token store. With a *rotating* refresh token (every refresh invalidates the old one) the process that
loses the race got ``invalid_grant`` and cleared the shared store, deleting the winner's fresh tokens.

The token endpoint below is a real HTTP server in a thread that rotates the refresh token, counts the requests
and answers slowly (so racing refreshes overlap). The racing refreshers are real processes.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2
import pytest
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

from farm.executors.mcp.connect import FarmTokenStorageAdapter, HeadlessOAuth

TOKEN_DELAY_S = 0.6


class _Server:
    """An OAuth token endpoint that rotates its refresh token, and a protected ``/mcp`` that checks the bearer."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.refresh = "rt-0"
        self.access = "at-0"
        self.rotations = 0
        self.token_requests = 0
        self.on_stale: Callable[[], None] | None = None
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

            def _send(self, status: int, body: dict[str, Any]) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length)
                if self.path == "/token":
                    form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
                    with server.lock:
                        server.token_requests += 1
                    time.sleep(TOKEN_DELAY_S)
                    with server.lock:
                        if form.get("refresh_token") != server.refresh:
                            stale = server.on_stale
                        else:
                            stale = None
                            server.rotations += 1
                            server.refresh = f"rt-{server.rotations}"
                            server.access = f"at-{server.rotations}"
                            self._send(
                                200,
                                {
                                    "access_token": server.access,
                                    "token_type": "Bearer",
                                    "expires_in": 3600,
                                    "refresh_token": server.refresh,
                                },
                            )
                            return
                    if stale is not None:
                        stale()
                    self._send(400, {"error": "invalid_grant"})
                    return
                bearer = self.headers.get("authorization", "")
                with server.lock:
                    good = bearer == f"Bearer {server.access}"
                self._send(200 if good else 401, {})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.origin = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.mcp_url = f"{self.origin}/mcp"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server() -> Iterator[_Server]:
    with _Server() as running:
        yield running


def _store(directory: Path) -> FileTreeStore:
    return FileTreeStore(
        data_directory=str(directory),
        key_sanitization_strategy=AlwaysHashStrategy(),
        collection_sanitization_strategy=AlwaysHashStrategy(),
    )


async def _seed_expired_login(server: _Server, directory: Path) -> FarmTokenStorageAdapter:
    """A stored login whose access token has expired, with the server's current refresh token."""
    store = _store(directory)
    adapter = FarmTokenStorageAdapter(async_key_value=store, server_url=server.mcp_url)
    await adapter.set_tokens(
        OAuthToken(access_token="at-0", token_type="Bearer", expires_in=3600, refresh_token="rt-0", scope="mcp")
    )
    await adapter.set_client_info(OAuthClientInformationFull(client_id="client-1", redirect_uris=None))
    await adapter.set_metadata(
        OAuthMetadata(
            issuer=server.origin,
            authorization_endpoint=f"{server.origin}/authorize",
            token_endpoint=f"{server.origin}/token",
            response_types_supported=["code"],
        )
    )
    await store.put(
        key=f"{server.mcp_url}/token_expiry",
        value={"expires_at": time.time() - 60},
        collection="mcp-oauth-token-expiry",
    )
    return adapter


CHILD = """
import asyncio, inspect, sys, time
from pathlib import Path
import httpx2
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.stores.filetree import FileTreeStore
from farm.executors.mcp.connect import HeadlessOAuth

store_dir, lock, url, start_at = sys.argv[1:5]
store = FileTreeStore(data_directory=store_dir, key_sanitization_strategy=AlwaysHashStrategy(),
                      collection_sanitization_strategy=AlwaysHashStrategy())
extra = {"refresh_lock": Path(lock)} if "refresh_lock" in inspect.signature(HeadlessOAuth.__init__).parameters else {}
auth = HeadlessOAuth(mcp_url=url, token_storage=store, client_name="Harness Farm (acct-01)",
                     connection_id="acct-01", **extra)

async def main():
    while time.time() < float(start_at):
        await asyncio.sleep(0.005)
    async with httpx2.AsyncClient(auth=auth) as client:
        print((await client.post(url, json={})).status_code)

asyncio.run(main())
"""


async def test_two_processes_refreshing_at_once_share_one_refresh(server: _Server, tmp_path: Path) -> None:
    store_dir = tmp_path / "tokens" / "acct-01"
    store_dir.mkdir(parents=True)
    adapter = await _seed_expired_login(server, store_dir)
    lock = tmp_path / "tokens" / "acct-01.refresh.lock"
    start_at = str(time.time() + 3.0)  # both processes are up before either refreshes

    children = [
        subprocess.Popen(
            [sys.executable, "-I", "-c", CHILD, str(store_dir), str(lock), server.mcp_url, start_at],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
            env=_child_env(),
        )
        for _ in range(2)
    ]
    outputs = await asyncio.gather(*(asyncio.to_thread(child.communicate, None, 90) for child in children))

    results = [
        (child.returncode, out.strip().splitlines()[-1:], err.strip()[-300:])
        for child, (out, err) in zip(children, outputs, strict=True)
    ]
    assert results == [(0, ["200"], ""), (0, ["200"], "")], results
    assert server.token_requests == 1  # the second process used the first one's tokens
    stored = await adapter.get_tokens()
    assert stored is not None and stored.refresh_token == "rt-1" and stored.access_token == "at-1"


def _child_env() -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if k in {"SYSTEMROOT", "PATH", "TEMP", "TMP"}}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    return env


async def test_a_lost_race_uses_the_winners_token_and_does_not_clear_the_store(
    server: _Server, tmp_path: Path
) -> None:
    """No lock (another tool's refresh): the winner stored rt-1 while this refresh with rt-0 was in flight."""
    adapter = await _seed_expired_login(server, tmp_path)
    loop = asyncio.get_running_loop()

    server.refresh, server.access, server.rotations = "rt-1", "at-1", 1  # the winner has already rotated

    def winner_stored_its_tokens() -> None:
        winner = OAuthToken(access_token="at-1", token_type="Bearer", expires_in=3600, refresh_token="rt-1")
        asyncio.run_coroutine_threadsafe(adapter.set_tokens(winner), loop).result(30)

    server.on_stale = winner_stored_its_tokens
    auth = HeadlessOAuth(
        mcp_url=server.mcp_url,
        token_storage=_store(tmp_path),
        client_name="Harness Farm (acct-01)",
        connection_id="acct-01",
    )

    async with httpx2.AsyncClient(auth=auth) as client:
        response = await client.post(server.mcp_url, json={})

    assert response.status_code == 200
    stored = await adapter.get_tokens()
    assert stored is not None and stored.refresh_token == "rt-1"


async def test_invalid_grant_with_nothing_newer_stored_still_clears_the_login(
    server: _Server, tmp_path: Path
) -> None:
    from farm.executors.mcp.connect import LoginRequired

    adapter = await _seed_expired_login(server, tmp_path)
    server.refresh = "rt-revoked"  # the server no longer knows the stored refresh token, and nobody replaced it
    auth = HeadlessOAuth(
        mcp_url=server.mcp_url,
        token_storage=_store(tmp_path),
        client_name="Harness Farm (acct-01)",
        connection_id="acct-01",
    )

    with pytest.raises(LoginRequired):
        async with httpx2.AsyncClient(auth=auth) as client:
            await client.post(server.mcp_url, json={})

    assert await adapter.get_tokens() is None
