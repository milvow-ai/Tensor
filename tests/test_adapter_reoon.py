"""Reoon adapter, fully offline (respx). Also covers the shared ApiAdapter template behaviour.

The recurring assertion: nothing the adapter returns, raises, logs or prints contains the API key.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import BaseModel

from farm.adapters import ADAPTERS, ApiAdapter, BalanceResult, ProviderError, ReoonAdapter, ZeroBounceAdapter
from farm.adapters._template import classify_status, clip, parse_retry_after
from farm.capabilities.schemas import VERIFY_STATUSES, VerifyEmailOut
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult, Executor
from farm.secrets import MASK, resolve_auth

KEY = "SENTINEL-reoon-key-7d2e91ac"
BASE = "https://emailverifier.reoon.com"
VERIFY = "/api/v1/verify"
BALANCE = "/api/v1/check-account-balance/"
EMAIL = "jane.doe@example.com"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "reoon"


def load_cases(name: str) -> dict[str, dict[str, Any]]:
    doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert doc["_source"], f"{name} must say where its data came from"
    cases: dict[str, dict[str, Any]] = doc["cases"]
    return cases


POWER = load_cases("verify_power.json")
QUICK = load_cases("verify_quick.json")
ERRORS = load_cases("errors.json")
BALANCES = load_cases("balance.json")


@pytest.fixture(autouse=True)
def _reoon_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REOON_API_KEY", KEY)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def adapter() -> AsyncIterator[ReoonAdapter]:
    # verify=False only skips building a TLS context per test (slow on Windows); respx never opens a socket.
    async with httpx.AsyncClient(verify=False) as client:
        yield ReoonAdapter(client, clock=lambda: NOW)


def make_request(
    email: Any = EMAIL,
    *,
    meta: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
    auth_ref: str = "env:REOON_API_KEY",
    capability: str = "verify_email",
    params: dict[str, Any] | None = None,
) -> ExecRequest:
    return ExecRequest(
        request_id=uuid4(),
        capability=capability,
        params={"email": email} if params is None else params,
        connection=ConnectionView(
            id="reoon-01", provider_id="reoon", auth_ref=auth_ref, meta=meta or {}, concurrency=2
        ),
        timeout_s=timeout_s,
    )


def connection(auth_ref: str = "env:REOON_API_KEY") -> ConnectionView:
    return ConnectionView(id="reoon-01", provider_id="reoon", auth_ref=auth_ref)


def assert_no_key(*things: object) -> None:
    for thing in things:
        text = thing.model_dump_json() if isinstance(thing, BaseModel) else repr(thing)
        assert KEY not in text, f"API key leaked into {type(thing).__name__}: {text}"


def assert_failed(result: ExecResult, kind: ErrorKind) -> None:
    assert result.ok is False
    assert result.error_kind is kind, f"{result.error_kind} != {kind}: {result.error}"
    assert result.error
    assert result.data is None and result.found is None
    assert result.units_used == {}  # a failed call is never counted as a charge
    assert_no_key(result)


# --- verify_email: every Reoon status, normalised ----------------------------------------------------

POWER_EXPECTED = [
    ("safe", "valid", None),
    ("invalid", "invalid", None),
    ("invalid_syntax", "invalid", "failed_syntax_check"),
    ("invalid_no_mx", "invalid", "does_not_accept_mail"),
    ("disabled", "invalid", "disabled"),
    ("disposable", "risky", "disposable"),
    ("inbox_full", "risky", "inbox_full"),
    ("catch_all", "catch_all", None),
    ("role_account", "risky", "role_based"),
    ("spamtrap", "risky", "spamtrap"),
    ("unknown", "unknown", None),
]


def test_every_power_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in POWER_EXPECTED} == set(POWER)
    assert {
        "safe",
        "invalid",
        "disabled",
        "disposable",
        "inbox_full",
        "catch_all",
        "role_account",
        "spamtrap",
        "unknown",
    } == {POWER[c]["status"] for c in POWER}  # the full documented status list


@pytest.mark.parametrize(("case", "status", "sub_status"), POWER_EXPECTED)
async def test_power_mode_statuses_map_to_the_normalised_schema(
    api: respx.MockRouter, adapter: ReoonAdapter, case: str, status: str, sub_status: str | None
) -> None:
    route = api.get(VERIFY).respond(200, json=POWER[case])
    result = await adapter.execute(make_request())

    assert result.ok is True and result.error_kind is None and result.error is None
    out = VerifyEmailOut.model_validate(result.data)
    assert (out.status, out.sub_status) == (status, sub_status)
    assert out.status in VERIFY_STATUSES
    assert (out.provider, out.email, out.checked_at) == ("reoon", EMAIL, NOW)
    definitive = status != "unknown"
    assert result.found is definitive
    assert result.units_used == ({"credits": 1.0} if definitive else {})  # unknown is refunded by Reoon
    assert result.cost_usd == 0
    assert isinstance(result.latency_ms, int) and result.latency_ms >= 0
    assert dict(route.calls.last.request.url.params) == {"email": EMAIL, "key": KEY, "mode": "power"}
    assert_no_key(result)


QUICK_EXPECTED = [
    ("valid", "risky", "mailbox_unchecked"),  # quick mode never checks the inbox, so never "valid"
    ("invalid", "invalid", "failed_syntax_check"),
    ("disposable", "risky", "disposable"),
    ("spamtrap", "risky", "spamtrap"),
]


def test_every_quick_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in QUICK_EXPECTED} == set(QUICK)


@pytest.mark.parametrize(("case", "status", "sub_status"), QUICK_EXPECTED)
async def test_quick_mode_statuses_map_to_the_normalised_schema(
    api: respx.MockRouter, adapter: ReoonAdapter, case: str, status: str, sub_status: str | None
) -> None:
    route = api.get(VERIFY).respond(200, json=QUICK[case])
    result = await adapter.execute(make_request(meta={"mode": "quick"}))
    assert result.ok is True
    out = VerifyEmailOut.model_validate(result.data)
    assert (out.status, out.sub_status) == (status, sub_status)
    assert result.found is True and result.units_used == {"credits": 1.0}
    assert route.calls.last.request.url.params["mode"] == "quick"
    assert_no_key(result)


async def test_the_mode_the_provider_reports_decides_how_the_status_is_read(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    api.get(VERIFY).respond(200, json=POWER["safe"])  # asked for quick, answered in power mode
    result = await adapter.execute(make_request(meta={"mode": "quick"}))
    assert VerifyEmailOut.model_validate(result.data).status == "valid"


@pytest.mark.parametrize("mode_meta", [{}, {"mode": "power"}, {"mode": " POWER "}])
async def test_power_is_the_default_mode(
    api: respx.MockRouter, adapter: ReoonAdapter, mode_meta: dict[str, Any]
) -> None:
    route = api.get(VERIFY).respond(200, json=POWER["safe"])
    await adapter.execute(make_request(meta=mode_meta))
    assert route.calls.last.request.url.params["mode"] == "power"


async def test_unknown_mode_in_connection_meta_is_a_bad_request_and_makes_no_call(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    route = api.get(VERIFY).respond(200, json=POWER["safe"])
    result = await adapter.execute(make_request(meta={"mode": "turbo"}))
    assert_failed(result, ErrorKind.BAD_REQUEST)
    assert not route.called


async def test_the_email_is_normalised_before_it_is_sent(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    route = api.get(VERIFY).respond(200, json=POWER["safe"])
    result = await adapter.execute(make_request("  Jane.Doe@EXAMPLE.com "))
    assert route.calls.last.request.url.params["email"] == "Jane.Doe@example.com"
    assert VerifyEmailOut.model_validate(result.data).email == "Jane.Doe@example.com"


@pytest.mark.parametrize(
    "bad_email",
    [
        "",
        "not-an-email",
        "a@b",
        "a b@example.com",
        "x@@example.com",
        "@example.com",
        "jane@.com",
        "jane@example..com",
        "jane@1.2.3.4",
        ".jane@example.com",
        "jane.@example.com",
        "ja..ne@example.com",
        "a" * 65 + "@example.com",
        "jane@" + "a" * 64 + ".com",
        12345,
        None,
    ],
)
async def test_invalid_input_is_a_bad_request_and_never_costs_a_call(
    api: respx.MockRouter, adapter: ReoonAdapter, bad_email: Any
) -> None:
    route = api.get(VERIFY).respond(200, json=POWER["safe"])
    result = await adapter.execute(make_request(bad_email))
    assert_failed(result, ErrorKind.BAD_REQUEST)
    assert "invalid verify_email params" in (result.error or "")
    assert not route.called


async def test_missing_email_param_is_a_bad_request(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    result = await adapter.execute(make_request(params={"address": EMAIL}))
    assert_failed(result, ErrorKind.BAD_REQUEST)
    assert api.calls.call_count == 0


# --- faults: HTTP status -> ErrorKind ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("http_status", "kind"),
    [
        (400, ErrorKind.BAD_REQUEST),
        (401, ErrorKind.AUTH),
        (402, ErrorKind.LIMIT_REACHED),
        (403, ErrorKind.AUTH),
        (404, ErrorKind.EMPTY),
        (408, ErrorKind.TIMEOUT),
        (422, ErrorKind.BAD_REQUEST),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.SERVER),
        (502, ErrorKind.SERVER),
        (503, ErrorKind.SERVER),
        (504, ErrorKind.SERVER),
    ],
)
@pytest.mark.parametrize(
    "body", [b"", b"<html>nope</html>", b'{"unrelated": true}'], ids=["empty", "html", "json"]
)
async def test_http_status_maps_to_error_kind(
    api: respx.MockRouter, adapter: ReoonAdapter, http_status: int, kind: ErrorKind, body: bytes
) -> None:
    api.get(VERIFY).respond(http_status, content=body)
    result = await adapter.execute(make_request())
    assert_failed(result, kind)
    assert f"HTTP {http_status}" in (result.error or "")
    assert (result.error or "").startswith("reoon: ")


async def test_a_redirect_is_reported_not_followed_so_the_key_cannot_travel(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    api.get(VERIFY).respond(302, headers={"location": f"https://elsewhere.example/steal?key={KEY}"})
    result = await adapter.execute(make_request())  # respx fails the test if the redirect target is requested
    assert_failed(result, ErrorKind.UNKNOWN)


async def test_429_carries_retry_after_seconds(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    api.get(VERIFY).respond(429, headers={"Retry-After": "17"})
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.RATE_LIMITED)
    assert result.retry_after_s == 17.0


async def test_429_retry_after_as_an_http_date(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    when = format_datetime(NOW + timedelta(seconds=90), usegmt=True)
    api.get(VERIFY).respond(429, headers={"Retry-After": when})
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.RATE_LIMITED)
    assert result.retry_after_s == 90.0


@pytest.mark.parametrize("header", [None, "", "soon", "Tue, 99 Foo 2026 25:61:61 GMT"])
async def test_429_without_a_usable_retry_after_still_classifies(
    api: respx.MockRouter, adapter: ReoonAdapter, header: str | None
) -> None:
    api.get(VERIFY).respond(429, headers={} if header is None else {"Retry-After": header})
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.RATE_LIMITED)
    assert result.retry_after_s is None


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.PoolTimeout("slow"),
        httpx.WriteTimeout("slow"),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_timeouts_are_classified_not_raised(
    api: respx.MockRouter, adapter: ReoonAdapter, exc: httpx.TimeoutException
) -> None:
    api.get(VERIFY).mock(side_effect=exc)
    result = await adapter.execute(make_request(timeout_s=4))
    assert_failed(result, ErrorKind.TIMEOUT)
    assert "4s" in (result.error or "")


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("dns"), httpx.ReadError("reset"), httpx.RemoteProtocolError("garbled")],
    ids=lambda e: type(e).__name__,
)
async def test_transport_failures_are_classified_as_server_errors(
    api: respx.MockRouter, adapter: ReoonAdapter, exc: httpx.RequestError
) -> None:
    api.get(VERIFY).mock(side_effect=exc)
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.SERVER)
    assert type(exc).__name__ in (result.error or "")


async def test_the_per_call_timeout_reaches_the_http_client(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    route = api.get(VERIFY).respond(200, json=POWER["safe"])
    await adapter.execute(make_request(timeout_s=12.5))
    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 12.5,
        "read": 12.5,
        "write": 12.5,
        "pool": 12.5,
    }


# --- faults: malformed or surprising bodies ----------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [b"", b"<html>Service Unavailable</html>", b'{"status": "sa', b"null-ish", b"\xff\xfe\x00bad"],
    ids=["empty", "html", "truncated", "text", "binary"],
)
async def test_malformed_json_is_unknown_not_an_exception(
    api: respx.MockRouter, adapter: ReoonAdapter, content: bytes
) -> None:
    api.get(VERIFY).respond(200, content=content)
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.UNKNOWN)
    assert "malformed JSON" in (result.error or "")


@pytest.mark.parametrize(
    "body",
    [[], "just a string", 42, {"verification_mode": "power"}, {"status": 123}, {"status": None}],
    ids=["list", "string", "number", "no-status", "numeric-status", "null-status"],
)
async def test_json_of_the_wrong_shape_is_unknown(
    api: respx.MockRouter, adapter: ReoonAdapter, body: Any
) -> None:
    api.get(VERIFY).respond(200, json=body)
    assert_failed(await adapter.execute(make_request()), ErrorKind.UNKNOWN)


@pytest.mark.parametrize(
    "status", ["quantum", "valid", ""], ids=["new", "quick-status-in-power-mode", "blank"]
)
async def test_a_status_we_do_not_recognise_is_surfaced_not_guessed(
    api: respx.MockRouter, adapter: ReoonAdapter, status: str
) -> None:
    api.get(VERIFY).respond(200, json={**POWER["safe"], "status": status})
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.UNKNOWN)
    assert "unrecognised Reoon status" in (result.error or "")


# --- faults: errors reported inside the body ---------------------------------------------------------

ERROR_BODY_EXPECTED = [
    ("auth_reason", 200, ErrorKind.AUTH),
    ("auth_reason", 400, ErrorKind.AUTH),
    ("auth_reason", 401, ErrorKind.AUTH),
    ("inactive_key_reason", 200, ErrorKind.AUTH),
    ("limit_reason", 200, ErrorKind.LIMIT_REACHED),
    ("limit_reason", 400, ErrorKind.LIMIT_REACHED),
    ("limit_reason", 402, ErrorKind.LIMIT_REACHED),
    ("rate_reason", 200, ErrorKind.RATE_LIMITED),
    ("rate_reason", 429, ErrorKind.RATE_LIMITED),
    ("bad_email_reason", 200, ErrorKind.BAD_REQUEST),
    ("unrecognised_reason", 200, ErrorKind.UNKNOWN),
    ("unrecognised_reason", 400, ErrorKind.BAD_REQUEST),
    ("reason_without_text", 200, ErrorKind.UNKNOWN),
    # the HTTP status wins when it is specific, whatever the free text says
    ("limit_reason", 429, ErrorKind.RATE_LIMITED),
    ("limit_reason", 401, ErrorKind.AUTH),
    ("rate_reason", 402, ErrorKind.LIMIT_REACHED),
    ("auth_reason", 500, ErrorKind.SERVER),
    ("reason_without_text", 503, ErrorKind.SERVER),
]


def test_every_error_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in ERROR_BODY_EXPECTED} | {"echoes_key"} == set(ERRORS)


@pytest.mark.parametrize(("case", "http_status", "kind"), ERROR_BODY_EXPECTED)
async def test_error_bodies_are_classified_from_status_then_reason(
    api: respx.MockRouter, adapter: ReoonAdapter, case: str, http_status: int, kind: ErrorKind
) -> None:
    api.get(VERIFY).respond(http_status, json=ERRORS[case])
    result = await adapter.execute(make_request())
    assert_failed(result, kind)
    reason = ERRORS[case].get("reason")
    if reason:
        assert reason in (result.error or "")


async def test_a_provider_error_text_that_echoes_the_key_is_masked(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    echoing = {"status": "error", "reason": ERRORS["echoes_key"]["reason"].replace("{KEY}", KEY)}
    api.get(VERIFY).respond(200, json=echoing)
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.AUTH)
    assert MASK in (result.error or "")


async def test_provider_error_text_is_bounded(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    api.get(VERIFY).respond(200, json={"status": "error", "reason": "api key " + "x" * 5000})
    result = await adapter.execute(make_request())
    assert len(result.error or "") < 500


# --- connection and capability problems --------------------------------------------------------------


async def test_missing_env_var_is_an_auth_error_that_names_it_and_makes_no_call(
    api: respx.MockRouter, adapter: ReoonAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REOON_API_KEY")
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.AUTH)
    assert "REOON_API_KEY" in (result.error or "")
    assert api.calls.call_count == 0


@pytest.mark.parametrize(
    "auth_ref", ["token-store:reoon-01", "cli:reoon", "PASTED-NOT-A-REF-0123456789", "env:not-valid-name"]
)
async def test_unusable_auth_refs_are_auth_errors_without_echo(
    api: respx.MockRouter, adapter: ReoonAdapter, auth_ref: str
) -> None:
    result = await adapter.execute(make_request(auth_ref=auth_ref))
    assert_failed(result, ErrorKind.AUTH)
    assert "PASTED-NOT-A-REF" not in (result.error or "")
    assert api.calls.call_count == 0


async def test_an_unsupported_capability_is_a_bad_request_and_makes_no_call(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    result = await adapter.execute(make_request(capability="find_email"))
    assert_failed(result, ErrorKind.BAD_REQUEST)
    assert "find_email" in (result.error or "")
    assert api.calls.call_count == 0


# --- no key leaks, anywhere --------------------------------------------------------------------------


async def test_exception_text_containing_the_url_and_key_never_reaches_the_result(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    api.get(VERIFY).mock(
        side_effect=httpx.ConnectError(f"cannot connect to {BASE}{VERIFY}?email={EMAIL}&key={KEY}&mode=power")
    )
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.SERVER)
    assert "cannot connect" not in (result.error or "")  # the httpx message is dropped, only its class kept


async def test_failures_do_not_print_or_log_the_key(
    api: respx.MockRouter,
    adapter: ReoonAdapter,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    scenarios: list[Any] = [
        httpx.ConnectError(f"boom key={KEY}"),
        httpx.ReadTimeout(f"slow {KEY}"),
        httpx.Response(500, text=f"upstream error for key={KEY}"),
        httpx.Response(200, json={"status": "error", "reason": f"bad api key {KEY}"}),
    ]
    route = api.get(VERIFY)
    for scenario in scenarios:
        route.side_effect = [scenario]
        result = await adapter.execute(make_request())
        assert result.ok is False
        assert_no_key(result)
    streams = capsys.readouterr()
    assert KEY not in streams.out and KEY not in streams.err
    assert KEY not in caplog.text
    assert "adapter.failure" in streams.out + streams.err  # the failures were logged (and clean)


async def test_httpx_request_log_lines_have_the_key_masked(
    api: respx.MockRouter, adapter: ReoonAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    api.get(VERIFY).respond(200, json=POWER["safe"])
    await adapter.execute(make_request())
    assert "HTTP Request: GET" in caplog.text  # httpx does log the URL at INFO...
    assert KEY not in caplog.text  # ...but the template's filter has masked the key
    assert f"key={MASK}" in caplog.text


def test_a_provider_error_never_carries_a_resolved_key_in_its_text() -> None:
    resolve_auth("env:REOON_API_KEY")
    error = ProviderError(ErrorKind.SERVER, f"upstream said {KEY} is bad; also ?api_key=abc123def")
    assert KEY not in str(error) and "abc123def" not in str(error)


# --- balance -----------------------------------------------------------------------------------------


async def test_balance_sums_daily_and_instant_credits(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    route = api.get(BALANCE).respond(200, json=BALANCES["ok"])
    result = await adapter.balance(connection())
    assert isinstance(result, BalanceResult)
    assert result.ok is True and result.error_kind is None
    assert result.remaining == 5150.0
    assert result.units == {"credits": 5150.0, "daily_credits": 150.0, "instant_credits": 5000.0}
    assert dict(route.calls.last.request.url.params) == {"key": KEY}
    assert_no_key(result)


async def test_balance_on_the_free_tier(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    api.get(BALANCE).respond(200, json=BALANCES["free_tier"])
    result = await adapter.balance(connection())
    assert result.remaining == 17.0 and result.units["daily_credits"] == 17.0


async def test_balance_with_an_inactive_key_is_an_auth_error(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    api.get(BALANCE).respond(200, json=BALANCES["inactive"])
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind, result.remaining) == (False, ErrorKind.AUTH, None)


async def test_balance_without_credit_fields_is_unknown(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    api.get(BALANCE).respond(200, json=BALANCES["no_credit_fields"])
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind) == (False, ErrorKind.UNKNOWN)


@pytest.mark.parametrize(
    ("http_status", "kind"),
    [(401, ErrorKind.AUTH), (403, ErrorKind.AUTH), (429, ErrorKind.RATE_LIMITED), (500, ErrorKind.SERVER)],
)
async def test_balance_http_errors_are_classified(
    api: respx.MockRouter, adapter: ReoonAdapter, http_status: int, kind: ErrorKind
) -> None:
    api.get(BALANCE).respond(http_status, headers={"Retry-After": "5"})
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind) == (False, kind)
    assert result.retry_after_s == 5.0  # the header is honoured whatever the status
    assert_no_key(result)


async def test_balance_timeout_malformed_json_and_missing_key(
    api: respx.MockRouter, adapter: ReoonAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = api.get(BALANCE)
    route.side_effect = httpx.ReadTimeout("slow")
    assert (await adapter.balance(connection())).error_kind is ErrorKind.TIMEOUT

    route.side_effect = None
    route.respond(200, content=b"<html>")
    assert (await adapter.balance(connection())).error_kind is ErrorKind.UNKNOWN

    monkeypatch.delenv("REOON_API_KEY")
    missing = await adapter.balance(connection())
    assert missing.error_kind is ErrorKind.AUTH and "REOON_API_KEY" in (missing.error or "")


# --- template behaviour shared by every API adapter --------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (200, None),
        (201, None),
        (204, None),
        (299, None),
        (100, ErrorKind.UNKNOWN),
        (301, ErrorKind.UNKNOWN),
        (302, ErrorKind.UNKNOWN),
        (399, ErrorKind.UNKNOWN),
        (400, ErrorKind.BAD_REQUEST),
        (401, ErrorKind.AUTH),
        (402, ErrorKind.LIMIT_REACHED),
        (403, ErrorKind.AUTH),
        (404, ErrorKind.EMPTY),
        (405, ErrorKind.BAD_REQUEST),
        (408, ErrorKind.TIMEOUT),
        (409, ErrorKind.BAD_REQUEST),
        (422, ErrorKind.BAD_REQUEST),
        (429, ErrorKind.RATE_LIMITED),
        (499, ErrorKind.BAD_REQUEST),
        (500, ErrorKind.SERVER),
        (502, ErrorKind.SERVER),
        (503, ErrorKind.SERVER),
        (504, ErrorKind.SERVER),
        (599, ErrorKind.SERVER),
    ],
)
def test_classify_status_table(status: int, kind: ErrorKind | None) -> None:
    assert classify_status(status) is kind


def test_parse_retry_after() -> None:
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after(" 2.5 ") == 2.5
    assert parse_retry_after("0") == 0.0
    assert parse_retry_after("-5") == 0.0  # never a negative wait
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("later") is None
    assert parse_retry_after("nan") is None and parse_retry_after("inf") is None
    assert parse_retry_after(format_datetime(NOW + timedelta(minutes=2), usegmt=True), now=NOW) == 120.0
    assert parse_retry_after(format_datetime(NOW - timedelta(minutes=2), usegmt=True), now=NOW) == 0.0


def test_clip_flattens_and_bounds_provider_text() -> None:
    assert clip("a\n  b\t c") == "a b c"
    assert len(clip("x" * 1000)) == 300 and clip("x" * 1000).endswith("…")
    assert clip("short") == "short"


def test_adapter_registry_and_executor_contract() -> None:
    assert ADAPTERS["reoon"] is ReoonAdapter and ADAPTERS["zerobounce"] is ZeroBounceAdapter
    for provider_id, cls in ADAPTERS.items():
        assert issubclass(cls, ApiAdapter) and cls.provider_id == provider_id
        adapter = cls(clock=lambda: NOW)
        assert isinstance(adapter, Executor)
        assert adapter.capabilities()  # every adapter serves at least one capability
    for verifier in (ReoonAdapter, ZeroBounceAdapter):
        assert "verify_email" in verifier().capabilities()


async def test_adapter_creates_and_closes_its_own_client_but_not_an_injected_one(
    api: respx.MockRouter,
) -> None:
    api.get(VERIFY).respond(200, json=POWER["safe"])
    async with ReoonAdapter(clock=lambda: NOW) as owned:
        assert (await owned.execute(make_request())).ok
        client = owned._client
        assert client is not None and not client.is_closed
    assert client.is_closed and owned._client is None

    async with httpx.AsyncClient(verify=False) as shared:
        borrowed = ReoonAdapter(shared)
        assert (await borrowed.execute(make_request())).ok
        await borrowed.aclose()
        assert not shared.is_closed  # the owner of the client closes it


async def test_base_url_is_overridable_by_the_constructor_only() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://eu.reoon.example.test/api/v1/verify").respond(200, json=POWER["safe"])
        async with httpx.AsyncClient(verify=False) as client:
            adapter = ReoonAdapter(client, base_url="https://eu.reoon.example.test/", clock=lambda: NOW)
            # a connection cannot redirect the key: `meta` is data, never a URL source
            result = await adapter.execute(make_request(meta={"base_url": "https://evil.example.test"}))
        assert result.ok and route.called


async def test_latency_is_measured(api: respx.MockRouter, adapter: ReoonAdapter) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=POWER["safe"])

    api.get(VERIFY).mock(side_effect=slow)
    result = await adapter.execute(make_request())
    assert result.ok and result.latency_ms >= 40


async def test_programming_errors_still_raise(api: respx.MockRouter) -> None:
    """Only provider faults are classified; a bug in a handler must not be swallowed."""

    class Broken(ApiAdapter):
        provider_id = "broken"
        base_url = "https://broken.example.test"

        def capabilities(self) -> Any:
            async def handler(req: ExecRequest, secret: str) -> ExecResult:
                raise RuntimeError("bug")

            return {"verify_email": handler}

    with pytest.raises(RuntimeError, match="bug"):
        await Broken().execute(make_request())


async def test_a_key_straddling_the_length_cut_is_masked_before_cutting(
    api: respx.MockRouter, adapter: ReoonAdapter
) -> None:
    """Clipping first would slice the key in half and leave an unrecognisable (unmasked) prefix behind."""
    api.get(VERIFY).respond(200, json={"status": "error", "reason": "api key rejected: " + "x" * 250 + KEY})
    result = await adapter.execute(make_request())
    assert result.ok is False
    assert "SENTINEL" not in (result.error or "")
    assert len(result.error or "") <= 320
