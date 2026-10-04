"""ZeroBounce adapter, fully offline (respx).

ZeroBounce's one documented error ("Invalid API Key or your account ran out of credits") is ambiguous, so the
adapter asks the free getcredits endpoint which of the two it is; those paths are tested explicitly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import BaseModel

from farm.adapters import BalanceResult, ZeroBounceAdapter
from farm.adapters.zerobounce import _api_timeout
from farm.capabilities.schemas import VERIFY_STATUSES, VerifyEmailOut
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.secrets import MASK

KEY = "SENTINEL-zerobounce-key-b83f50e2"
BASE = "https://api.zerobounce.net"
VALIDATE = "/v2/validate"
CREDITS = "/v2/getcredits"
EMAIL = "jane.doe@example.com"
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "zerobounce"


def load_cases(name: str) -> dict[str, dict[str, Any]]:
    doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert doc["_source"], f"{name} must say where its data came from"
    cases: dict[str, dict[str, Any]] = doc["cases"]
    return cases


VALIDATE_CASES = load_cases("validate.json")
CREDIT_CASES = load_cases("credits.json")
ERRORS = load_cases("errors.json")


@pytest.fixture(autouse=True)
def _zerobounce_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEROBOUNCE_API_KEY", KEY)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def adapter() -> AsyncIterator[ZeroBounceAdapter]:
    # verify=False only skips building a TLS context per test (slow on Windows); respx never opens a socket.
    async with httpx.AsyncClient(verify=False) as client:
        yield ZeroBounceAdapter(client, clock=lambda: NOW)


def make_request(
    email: Any = EMAIL,
    *,
    timeout_s: float = 30.0,
    auth_ref: str = "env:ZEROBOUNCE_API_KEY",
    capability: str = "verify_email",
) -> ExecRequest:
    return ExecRequest(
        request_id=uuid4(),
        capability=capability,
        params={"email": email},
        connection=ConnectionView(id="zerobounce-01", provider_id="zerobounce", auth_ref=auth_ref),
        timeout_s=timeout_s,
    )


def connection(auth_ref: str = "env:ZEROBOUNCE_API_KEY") -> ConnectionView:
    return ConnectionView(id="zerobounce-01", provider_id="zerobounce", auth_ref=auth_ref)


def assert_no_key(*things: object) -> None:
    for thing in things:
        text = thing.model_dump_json() if isinstance(thing, BaseModel) else repr(thing)
        assert KEY not in text, f"API key leaked into {type(thing).__name__}: {text}"


def assert_failed(result: ExecResult, kind: ErrorKind) -> None:
    assert result.ok is False
    assert result.error_kind is kind, f"{result.error_kind} != {kind}: {result.error}"
    assert result.error
    assert result.data is None and result.found is None
    assert result.units_used == {}
    assert_no_key(result)


# --- verify_email: every ZeroBounce status, normalised -----------------------------------------------

VALIDATE_EXPECTED = [
    ("valid", "valid", None),
    ("valid_alias", "valid", "alias_address"),
    ("valid_accept_all", "valid", "accept_all"),
    ("valid_gold", "valid", "gold"),
    ("invalid_mailbox_not_found", "invalid", "mailbox_not_found"),
    ("invalid_syntax", "invalid", "failed_syntax_check"),
    ("invalid_typo", "invalid", "possible_typo"),
    ("invalid_quota_exceeded", "risky", "mailbox_quota_exceeded"),  # same verdict as Reoon's inbox_full
    ("catch_all", "catch_all", None),
    ("unknown_greylisted", "unknown", "greylisted"),
    ("unknown_timeout", "unknown", "timeout_exceeded"),
    ("unknown_no_sub_status", "unknown", None),
    ("spamtrap", "risky", "spamtrap"),
    ("abuse", "risky", "abuse"),
    ("do_not_mail_disposable", "risky", "disposable"),
    ("do_not_mail_role_based", "risky", "role_based"),
    ("do_not_mail_toxic", "risky", "toxic"),
    ("do_not_mail_no_sub_status", "risky", None),
]


def test_every_validate_fixture_case_is_asserted() -> None:
    assert {case for case, _, _ in VALIDATE_EXPECTED} | {"unmapped_status"} == set(VALIDATE_CASES)
    documented = {"valid", "invalid", "catch-all", "unknown", "spamtrap", "abuse", "do_not_mail"}
    assert documented == {c["status"] for k, c in VALIDATE_CASES.items() if k != "unmapped_status"}


@pytest.mark.parametrize(("case", "status", "sub_status"), VALIDATE_EXPECTED)
async def test_statuses_map_to_the_normalised_schema(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, case: str, status: str, sub_status: str | None
) -> None:
    route = api.get(VALIDATE).respond(200, json=VALIDATE_CASES[case])
    result = await adapter.execute(make_request())

    assert result.ok is True and result.error_kind is None and result.error is None
    out = VerifyEmailOut.model_validate(result.data)
    assert (out.status, out.sub_status) == (status, sub_status)
    assert out.status in VERIFY_STATUSES
    assert (out.provider, out.email, out.checked_at) == ("zerobounce", EMAIL, NOW)
    definitive = status != "unknown"
    assert result.found is definitive
    assert result.units_used == ({"credits": 1.0} if definitive else {})  # unknown is never charged
    assert result.cost_usd == 0
    assert dict(route.calls.last.request.url.params) == {"api_key": KEY, "email": EMAIL, "timeout": "27"}
    assert_no_key(result)


async def test_a_status_we_do_not_recognise_is_surfaced_not_guessed(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    api.get(VALIDATE).respond(200, json=VALIDATE_CASES["unmapped_status"])
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.UNKNOWN)
    assert "unrecognised ZeroBounce status" in (result.error or "")


async def test_status_spelling_variants_are_tolerated(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    for spelled in ("catch-all", "catch_all", "CATCH-ALL", " Catch-All "):
        api.get(VALIDATE).respond(200, json={**VALIDATE_CASES["catch_all"], "status": spelled})
        result = await adapter.execute(make_request())
        assert VerifyEmailOut.model_validate(result.data).status == "catch_all", spelled


async def test_the_result_email_is_the_normalised_request_email_not_the_response_echo(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    api.get(VALIDATE).respond(200, json={**VALIDATE_CASES["valid"], "address": "someone.else@example.org"})
    result = await adapter.execute(make_request("  Jane.Doe@EXAMPLE.com"))
    assert VerifyEmailOut.model_validate(result.data).email == "Jane.Doe@example.com"


@pytest.mark.parametrize(
    ("timeout_s", "sent"),
    [(30.0, "27"), (10.0, "7"), (5.0, "3"), (1.0, "3"), (90.0, "60"), (63.0, "60"), (12.5, "9")],
)
async def test_zerobounce_is_told_to_give_up_before_our_client_does(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, timeout_s: float, sent: str
) -> None:
    route = api.get(VALIDATE).respond(200, json=VALIDATE_CASES["valid"])
    await adapter.execute(make_request(timeout_s=timeout_s))
    assert route.calls.last.request.url.params["timeout"] == sent
    assert route.calls.last.request.extensions["timeout"]["read"] == timeout_s
    assert str(_api_timeout(timeout_s)) == sent


@pytest.mark.parametrize("bad_email", ["", "not-an-email", "a@b", "x@@example.com", "jane@1.2.3.4", 7, None])
async def test_invalid_input_is_a_bad_request_and_never_costs_a_call(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, bad_email: Any
) -> None:
    route = api.get(VALIDATE).respond(200, json=VALIDATE_CASES["valid"])
    assert_failed(await adapter.execute(make_request(bad_email)), ErrorKind.BAD_REQUEST)
    assert not route.called


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
    api: respx.MockRouter, adapter: ZeroBounceAdapter, http_status: int, kind: ErrorKind, body: bytes
) -> None:
    api.get(VALIDATE).respond(http_status, content=body)
    result = await adapter.execute(make_request())
    assert_failed(result, kind)
    assert f"HTTP {http_status}" in (result.error or "")
    assert (result.error or "").startswith("zerobounce: ")


async def test_a_redirect_is_reported_not_followed(api: respx.MockRouter, adapter: ZeroBounceAdapter) -> None:
    api.get(VALIDATE).respond(302, headers={"location": f"https://elsewhere.example/steal?api_key={KEY}"})
    assert_failed(await adapter.execute(make_request()), ErrorKind.UNKNOWN)


async def test_429_uses_retry_after_when_given(api: respx.MockRouter, adapter: ZeroBounceAdapter) -> None:
    api.get(VALIDATE).respond(429, headers={"Retry-After": "12"})
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.RATE_LIMITED)
    assert result.retry_after_s == 12.0


@pytest.mark.parametrize("header", [None, "", "soon"])
async def test_429_without_retry_after_assumes_the_documented_one_minute_block(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, header: str | None
) -> None:
    api.get(VALIDATE).respond(429, headers={} if header is None else {"Retry-After": header})
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.RATE_LIMITED)
    assert result.retry_after_s == 60.0


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow"), httpx.PoolTimeout("slow")],
    ids=lambda e: type(e).__name__,
)
async def test_timeouts_are_classified_not_raised(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, exc: httpx.TimeoutException
) -> None:
    api.get(VALIDATE).mock(side_effect=exc)
    assert_failed(await adapter.execute(make_request(timeout_s=6)), ErrorKind.TIMEOUT)


@pytest.mark.parametrize(
    "exc", [httpx.ConnectError("dns"), httpx.ReadError("reset")], ids=lambda e: type(e).__name__
)
async def test_transport_failures_are_classified_as_server_errors(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, exc: httpx.RequestError
) -> None:
    api.get(VALIDATE).mock(side_effect=exc)
    assert_failed(await adapter.execute(make_request()), ErrorKind.SERVER)


@pytest.mark.parametrize(
    "content",
    [b"", b"<html>Service Unavailable</html>", b'{"status": "va', b"\xff\xfe\x00bad"],
    ids=["empty", "html", "truncated", "binary"],
)
async def test_malformed_json_is_unknown_not_an_exception(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, content: bytes
) -> None:
    api.get(VALIDATE).respond(200, content=content)
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.UNKNOWN)
    assert "malformed JSON" in (result.error or "")


@pytest.mark.parametrize(
    "body",
    [[], "text", 42, {"address": EMAIL}, {"status": 5}, {"status": None}],
    ids=["list", "string", "number", "no-status", "numeric-status", "null-status"],
)
async def test_json_of_the_wrong_shape_is_unknown(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, body: Any
) -> None:
    api.get(VALIDATE).respond(200, json=body)
    assert_failed(await adapter.execute(make_request()), ErrorKind.UNKNOWN)


# --- the ambiguous "invalid key OR out of credits" error ---------------------------------------------

AMBIGUOUS = ERRORS["key_or_credits"]


def test_every_error_fixture_case_is_used_below() -> None:
    assert set(ERRORS) == {"key_or_credits", "key_only", "credits_only", "other_error", "echoes_key"}


@pytest.mark.parametrize("http_status", [200, 400, 401, 403])
@pytest.mark.parametrize(
    ("credits", "kind"),
    [
        ("invalid_key", ErrorKind.AUTH),  # getcredits returns -1: the key is wrong
        ("zero", ErrorKind.LIMIT_REACHED),  # getcredits returns 0: the account is out of credits
        ("ok", ErrorKind.UNKNOWN),  # credits remain, so neither explanation holds
    ],
)
async def test_the_ambiguous_error_is_resolved_with_the_free_balance_endpoint(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, http_status: int, credits: str, kind: ErrorKind
) -> None:
    api.get(VALIDATE).respond(http_status, json=AMBIGUOUS)
    probe = api.get(CREDITS).respond(200, json=CREDIT_CASES[credits])
    result = await adapter.execute(make_request(timeout_s=30))
    assert_failed(result, kind)
    assert probe.call_count == 1
    assert dict(probe.calls.last.request.url.params) == {"api_key": KEY}


@pytest.mark.parametrize(
    "probe_failure",
    [
        httpx.Response(500),
        httpx.Response(429),
        httpx.Response(200, content=b"<html>"),
        httpx.Response(200, json=CREDIT_CASES["missing"]),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
    ],
    ids=["500", "429", "html", "no-credits-field", "timeout", "connect-error"],
)
async def test_when_the_probe_fails_too_the_result_stays_auth_and_says_why(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, probe_failure: Any
) -> None:
    api.get(VALIDATE).respond(200, json=AMBIGUOUS)
    api.get(CREDITS).mock(side_effect=[probe_failure])
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.AUTH)
    assert "ran out of credits" in (result.error or "")  # the provider's own wording is kept


async def test_the_probe_is_capped_at_ten_seconds(api: respx.MockRouter, adapter: ZeroBounceAdapter) -> None:
    api.get(VALIDATE).respond(200, json=AMBIGUOUS)
    probe = api.get(CREDITS).respond(200, json=CREDIT_CASES["zero"])
    await adapter.execute(make_request(timeout_s=45))
    assert probe.calls.last.request.extensions["timeout"]["read"] == 10.0


@pytest.mark.parametrize(
    ("case", "kind"), [("key_only", ErrorKind.AUTH), ("credits_only", ErrorKind.LIMIT_REACHED)]
)
async def test_unambiguous_errors_need_no_probe(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, case: str, kind: ErrorKind
) -> None:
    api.get(VALIDATE).respond(200, json=ERRORS[case])
    probe = api.get(CREDITS).respond(200, json=CREDIT_CASES["ok"])
    assert_failed(await adapter.execute(make_request()), kind)
    assert not probe.called


@pytest.mark.parametrize(
    ("http_status", "kind"),
    [
        (402, ErrorKind.LIMIT_REACHED),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.SERVER),
        (408, ErrorKind.TIMEOUT),
    ],
)
async def test_a_specific_http_status_beats_the_ambiguous_text(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, http_status: int, kind: ErrorKind
) -> None:
    api.get(VALIDATE).respond(http_status, json=AMBIGUOUS)
    probe = api.get(CREDITS).respond(200, json=CREDIT_CASES["invalid_key"])
    assert_failed(await adapter.execute(make_request()), kind)
    assert not probe.called


@pytest.mark.parametrize(
    ("http_status", "kind"), [(200, ErrorKind.UNKNOWN), (400, ErrorKind.BAD_REQUEST), (404, ErrorKind.EMPTY)]
)
async def test_an_unrecognised_error_text_falls_back_to_the_http_status(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, http_status: int, kind: ErrorKind
) -> None:
    api.get(VALIDATE).respond(http_status, json=ERRORS["other_error"])
    result = await adapter.execute(make_request())
    assert_failed(result, kind)
    assert ERRORS["other_error"]["error"] in (result.error or "")


async def test_a_provider_error_text_that_echoes_the_key_is_masked(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    echoing = {"error": ERRORS["echoes_key"]["error"].replace("{KEY}", KEY)}
    api.get(VALIDATE).respond(200, json=echoing)
    api.get(CREDITS).respond(200, json=CREDIT_CASES["invalid_key"])
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.AUTH)

    api.get(VALIDATE).respond(400, json=echoing)
    api.get(CREDITS).respond(500)  # probe fails: the provider's (masked) wording is what remains
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.AUTH)
    assert MASK in (result.error or "")


# --- connection and capability problems --------------------------------------------------------------


async def test_missing_env_var_is_an_auth_error_that_names_it_and_makes_no_call(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ZEROBOUNCE_API_KEY")
    result = await adapter.execute(make_request())
    assert_failed(result, ErrorKind.AUTH)
    assert "ZEROBOUNCE_API_KEY" in (result.error or "")
    assert api.calls.call_count == 0


async def test_an_unsupported_capability_is_a_bad_request(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    result = await adapter.execute(make_request(capability="enrich_company"))
    assert_failed(result, ErrorKind.BAD_REQUEST)
    assert api.calls.call_count == 0


# --- no key leaks, anywhere --------------------------------------------------------------------------


async def test_failures_do_not_print_or_log_the_key(
    api: respx.MockRouter,
    adapter: ZeroBounceAdapter,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    validate = api.get(VALIDATE)
    api.get(CREDITS).respond(200, json=CREDIT_CASES["invalid_key"])
    scenarios: list[Any] = [
        httpx.ConnectError(f"boom api_key={KEY}"),
        httpx.ReadTimeout(f"slow {KEY}"),
        httpx.Response(500, text=f"upstream error for api_key={KEY}"),
        httpx.Response(200, json={"error": f"Invalid API Key {KEY} or your account ran out of credits"}),
        httpx.Response(401, json={"error": f"bad key {KEY}"}),
    ]
    for scenario in scenarios:
        validate.side_effect = [scenario]
        result = await adapter.execute(make_request())
        assert result.ok is False
        assert_no_key(result)
    streams = capsys.readouterr()
    assert KEY not in streams.out and KEY not in streams.err
    assert KEY not in caplog.text
    assert "adapter.failure" in streams.out + streams.err


async def test_httpx_request_log_lines_have_the_key_masked(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    api.get(VALIDATE).respond(200, json=VALIDATE_CASES["valid"])
    await adapter.execute(make_request())
    assert "HTTP Request: GET" in caplog.text
    assert KEY not in caplog.text
    assert f"api_key={MASK}" in caplog.text


# --- balance -----------------------------------------------------------------------------------------


async def test_balance_parses_remaining_credits(api: respx.MockRouter, adapter: ZeroBounceAdapter) -> None:
    route = api.get(CREDITS).respond(200, json=CREDIT_CASES["ok"])
    result = await adapter.balance(connection())
    assert isinstance(result, BalanceResult)
    assert result.ok is True and result.error_kind is None
    assert result.remaining == 2375323.0 and result.units == {"credits": 2375323.0}
    assert dict(route.calls.last.request.url.params) == {"api_key": KEY}
    assert_no_key(result)


@pytest.mark.parametrize(("case", "expected"), [("zero", 0.0), ("numeric_string", 1500.0)])
async def test_balance_edge_values(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, case: str, expected: float
) -> None:
    api.get(CREDITS).respond(200, json=CREDIT_CASES[case])
    result = await adapter.balance(connection())
    assert result.ok is True and result.remaining == expected


async def test_balance_minus_one_means_an_invalid_key(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    api.get(CREDITS).respond(200, json=CREDIT_CASES["invalid_key"])
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind, result.remaining) == (False, ErrorKind.AUTH, None)


async def test_balance_without_a_credits_field_is_unknown(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    api.get(CREDITS).respond(200, json=CREDIT_CASES["missing"])
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind) == (False, ErrorKind.UNKNOWN)


@pytest.mark.parametrize(
    ("http_status", "kind"),
    [(401, ErrorKind.AUTH), (429, ErrorKind.RATE_LIMITED), (500, ErrorKind.SERVER)],
)
async def test_balance_http_errors_are_classified(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, http_status: int, kind: ErrorKind
) -> None:
    api.get(CREDITS).respond(http_status, headers={"Retry-After": "5"})
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind) == (False, kind)
    assert result.retry_after_s == 5.0
    assert_no_key(result)


async def test_balance_timeout_malformed_json_and_missing_key(
    api: respx.MockRouter, adapter: ZeroBounceAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = api.get(CREDITS)
    route.side_effect = httpx.ReadTimeout("slow")
    assert (await adapter.balance(connection())).error_kind is ErrorKind.TIMEOUT

    route.side_effect = None
    route.respond(200, content=b"<html>")
    assert (await adapter.balance(connection())).error_kind is ErrorKind.UNKNOWN

    monkeypatch.delenv("ZEROBOUNCE_API_KEY")
    missing = await adapter.balance(connection())
    assert missing.error_kind is ErrorKind.AUTH and "ZEROBOUNCE_API_KEY" in (missing.error or "")


async def test_a_key_straddling_the_length_cut_is_masked_before_cutting(
    api: respx.MockRouter, adapter: ZeroBounceAdapter
) -> None:
    """Clipping first would slice the key in half and leave an unrecognisable (unmasked) prefix behind."""
    api.get(VALIDATE).respond(400, json={"error": "rejected: " + "x" * 250 + KEY})
    result = await adapter.execute(make_request())
    assert result.ok is False
    assert "SENTINEL" not in (result.error or "")
    assert len(result.error or "") <= 320
