"""Shared pieces of the offline adapter tests (respx, no network, no real key).

Each provider test file supplies its own fixtures and expectations; what is the same for every adapter lives
here: building requests, loading cited fixtures, the "failure looks like this" assertion and the fault
battery (timeouts, transport errors, malformed bodies, redirects, Retry-After, secret leaks).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import respx
from pydantic import BaseModel

from farm.adapters._template import ApiAdapter
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures"

TIMEOUT_EXCS: list[httpx.TimeoutException] = [
    httpx.ReadTimeout("slow"),
    httpx.ConnectTimeout("slow"),
    httpx.PoolTimeout("slow"),
    httpx.WriteTimeout("slow"),
]
TRANSPORT_EXCS: list[httpx.RequestError] = [
    httpx.ConnectError("dns"),
    httpx.ReadError("reset"),
    httpx.RemoteProtocolError("garbled"),
]
MALFORMED_BODIES: list[bytes] = [
    b"",
    b"<html>Service Unavailable</html>",
    b'{"people": [',
    b"\xff\xfe\x00bad",
]
MALFORMED_IDS = ["empty", "html", "truncated", "binary"]


def exc_id(exc: BaseException) -> str:
    return type(exc).__name__


def load_cases(provider: str, name: str) -> dict[str, Any]:
    """The ``cases`` of ``tests/fixtures/<provider>/<name>``; a fixture must cite where its data came from."""
    doc = json.loads((FIXTURES / provider / name).read_text(encoding="utf-8"))
    assert doc["_source"], f"{provider}/{name} must say where its data came from"
    cases: dict[str, Any] = doc["cases"]
    return cases


def make_request(
    *,
    provider: str,
    connection_id: str,
    auth_ref: str,
    capability: str,
    params: dict[str, Any],
    meta: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> ExecRequest:
    return ExecRequest(
        request_id=uuid4(),
        capability=capability,
        params=params,
        connection=ConnectionView(
            id=connection_id, provider_id=provider, auth_ref=auth_ref, meta=meta or {}, concurrency=1
        ),
        timeout_s=timeout_s,
    )


def assert_no_secret(secrets: tuple[str, ...], *things: object) -> None:
    for thing in things:
        text = thing.model_dump_json() if isinstance(thing, BaseModel) else repr(thing)
        for secret in secrets:
            assert secret not in text, f"secret leaked into {type(thing).__name__}: {text}"


def assert_failed(result: ExecResult, kind: ErrorKind, secrets: tuple[str, ...] = ()) -> None:
    assert result.ok is False
    assert result.error_kind is kind, f"{result.error_kind} != {kind}: {result.error}"
    assert result.error
    assert result.data is None and result.found is None
    assert result.units_used == {}  # a failed call is never counted as a charge
    assert_no_secret(secrets, result)


def assert_empty(result: ExecResult, *, units: dict[str, float] | None = None) -> None:
    """The provider answered "nothing": a successful call (it may have been charged), no data found."""
    assert result.ok is True
    assert result.found is False
    assert result.error_kind is ErrorKind.EMPTY
    assert result.data is not None
    assert result.units_used == (units or {})


@dataclass
class Harness:
    """One adapter + the respx route of the call under test + a factory for a fresh request."""

    adapter: ApiAdapter
    router: respx.MockRouter
    route: respx.Route
    request: Callable[..., ExecRequest]
    secrets: tuple[str, ...]

    async def run(self, **overrides: Any) -> ExecResult:
        return await self.adapter.execute(self.request(**overrides))

    async def expect_failure(self, kind: ErrorKind, **overrides: Any) -> ExecResult:
        result = await self.run(**overrides)
        assert_failed(result, kind, self.secrets)
        return result


# --- the shared fault battery ------------------------------------------------------------------------------


async def check_timeout(h: Harness, exc: httpx.TimeoutException) -> None:
    h.route.mock(side_effect=exc)
    result = await h.expect_failure(ErrorKind.TIMEOUT, timeout_s=4)
    assert "4s" in (result.error or "")


async def check_transport_error(h: Harness, exc: httpx.RequestError) -> None:
    h.route.mock(side_effect=exc)
    result = await h.expect_failure(ErrorKind.SERVER)
    assert type(exc).__name__ in (result.error or "")


async def check_malformed_body(h: Harness, content: bytes) -> None:
    h.route.respond(200, content=content)
    result = await h.expect_failure(ErrorKind.UNKNOWN)
    assert "malformed JSON" in (result.error or "")


async def check_redirect_is_not_followed(h: Harness) -> None:
    secret = h.secrets[0]
    h.route.respond(302, headers={"location": f"https://elsewhere.example/steal?key={secret}"})
    await h.expect_failure(ErrorKind.UNKNOWN)  # respx fails the test if the redirect target is requested


async def check_retry_after_seconds(h: Harness, kind: ErrorKind, status: int) -> None:
    h.route.respond(status, headers={"Retry-After": "17"})
    result = await h.expect_failure(kind)
    assert result.retry_after_s == 17.0


async def check_no_secret_in_streams(
    h: Harness,
    capsys: Any,
    caplog: Any,
    ok_response: httpx.Response,
) -> None:
    """Failures and successes alike: no print, no log record, no result holds a secret."""
    caplog.set_level(logging.DEBUG)
    secret = h.secrets[0]
    scenarios: list[Any] = [
        httpx.ConnectError(f"boom key={secret}"),
        httpx.ReadTimeout(f"slow {secret}"),
        httpx.Response(500, text=f"upstream error for key={secret} and x-api-key {secret}"),
        httpx.Response(401, text=f"bad credential {secret}"),
        ok_response,
    ]
    for scenario in scenarios:
        h.route.side_effect = [scenario]
        result = await h.run()
        assert_no_secret(h.secrets, result)
    streams = capsys.readouterr()
    for secret in h.secrets:
        assert secret not in streams.out and secret not in streams.err
        assert secret not in caplog.text
    assert "adapter.failure" in streams.out + streams.err  # the failures were logged (and clean)
