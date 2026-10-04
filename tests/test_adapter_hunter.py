"""Hunter adapter, fully offline (respx): find_email (1 search only when found), verify_email, balance.

Hunter's status codes are unusual (403 = rate limit, 429 = usage limit, 202 = verification still running);
those readings come from its docs and are pinned here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from functools import partial
from typing import Any

import httpx
import pytest
import respx

from farm.adapters import ADAPTERS, HunterAdapter
from farm.capabilities.schemas import FindEmailOut, VerifyEmailOut
from farm.executors.base import ConnectionView, ErrorKind, Executor
from farm.secrets import MASK
from tests.adapter_testkit import (
    MALFORMED_BODIES,
    MALFORMED_IDS,
    NOW,
    TIMEOUT_EXCS,
    TRANSPORT_EXCS,
    Harness,
    assert_empty,
    check_malformed_body,
    check_no_secret_in_streams,
    check_redirect_is_not_followed,
    check_retry_after_seconds,
    check_timeout,
    check_transport_error,
    exc_id,
    load_cases,
    make_request,
)

KEY = "SENTINEL-hunter-key-0be41a97"
BASE = "https://api.hunter.io"
FINDER = "/v2/email-finder"
VERIFIER = "/v2/email-verifier"
ACCOUNT = "/v2/account"

FINDER_CASES = load_cases("hunter", "finder.json")
VERIFIER_CASES = load_cases("hunter", "verifier.json")
ACCOUNT_CASES = load_cases("hunter", "account.json")
ERRORS = load_cases("hunter", "errors.json")

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "find_email": {"first_name": "Alexis", "last_name": "Ohanian", "domain": "reddit.com"},
    "verify_email": {"email": "patrick@stripe.com"},
}
ROUTES = {"find_email": FINDER, "verify_email": VERIFIER}
OK_BODIES = {"find_email": FINDER_CASES["found"], "verify_email": VERIFIER_CASES["valid"]}


@pytest.fixture(autouse=True)
def _hunter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_API_KEY", KEY)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def adapter() -> AsyncIterator[HunterAdapter]:
    async with httpx.AsyncClient(verify=False) as client:
        yield HunterAdapter(client, clock=lambda: NOW)


def harness_for(capability: str, api: respx.MockRouter, adapter: HunterAdapter) -> Harness:
    request = partial(
        make_request,
        provider="hunter",
        connection_id="hunter-01",
        auth_ref="env:HUNTER_API_KEY",
        capability=capability,
        params=DEFAULT_PARAMS[capability],
    )
    return Harness(
        adapter=adapter, router=api, route=api.get(path=ROUTES[capability]), request=request, secrets=(KEY,)
    )


@pytest.fixture(params=list(ROUTES))
def h(request: pytest.FixtureRequest, api: respx.MockRouter, adapter: HunterAdapter) -> Harness:
    """The fault battery below runs once per capability."""
    return harness_for(str(request.param), api, adapter)


@pytest.fixture
def finder(api: respx.MockRouter, adapter: HunterAdapter) -> Harness:
    return harness_for("find_email", api, adapter)


@pytest.fixture
def verifier(api: respx.MockRouter, adapter: HunterAdapter) -> Harness:
    return harness_for("verify_email", api, adapter)


# --- find_email --------------------------------------------------------------------------------------------


async def test_finder_returns_the_email_and_charges_one_search(finder: Harness) -> None:
    route = finder.route.respond(200, json=FINDER_CASES["found"])
    result = await finder.run()

    assert result.ok is True and result.found is True and result.error_kind is None and result.error is None
    assert result.units_used == {"searches": 1.0} and result.cost_usd == 0
    out = FindEmailOut.model_validate(result.data)
    assert (out.source.provider, out.source.connection_id, out.observed_at) == ("hunter", "hunter-01", NOW)
    assert out.email == "alexis@reddit.com" and out.confidence == 97
    assert (out.verification, out.verification_detail) == ("valid", "valid")
    assert (out.position, out.linkedin) == ("Cofounder", None)
    assert (out.first_name, out.last_name, out.domain) == ("Alexis", "Ohanian", "reddit.com")

    request = route.calls.last.request
    assert request.headers["X-API-KEY"] == KEY and KEY not in str(request.url)  # header, never in the URL
    assert dict(request.url.params) == {
        "domain": "reddit.com",
        "first_name": "Alexis",
        "last_name": "Ohanian",
    }


async def test_finder_maps_accept_all_and_unverified_results(finder: Harness) -> None:
    finder.route.respond(200, json=FINDER_CASES["accept_all"])
    out = FindEmailOut.model_validate((await finder.run()).data)
    assert (out.verification, out.verification_detail, out.confidence) == ("catch_all", "accept_all", 62)
    assert out.linkedin == "https://www.linkedin.com/in/alexisohanian"

    finder.route.respond(200, json=FINDER_CASES["unverified_guess"])
    out = FindEmailOut.model_validate((await finder.run()).data)
    assert (out.verification, out.verification_detail, out.confidence) == ("unknown", "unknown", 45)


@pytest.mark.parametrize("case", ["no_email", "blank_email"])
async def test_no_email_is_an_empty_result_and_no_search_is_charged(finder: Harness, case: str) -> None:
    finder.route.respond(200, json=FINDER_CASES[case])
    result = await finder.run()
    assert_empty(
        result, units={"searches": 0.0}
    )  # the docs: "If no email can be found, no credit is charged"
    assert FindEmailOut.model_validate(result.data).email is None


async def test_a_body_without_data_is_unknown(finder: Harness) -> None:
    finder.route.respond(200, json=FINDER_CASES["no_data_key"])
    await finder.expect_failure(ErrorKind.UNKNOWN)


@pytest.mark.parametrize("status", [404, 451])
async def test_a_person_who_cannot_be_looked_up_is_an_empty_result_not_a_failure(
    finder: Harness, status: int
) -> None:
    """404 = no such thing; 451 = the owner of the address asked not to be processed (claimed_email)."""
    finder.route.respond(status, json=ERRORS["claimed_email"])
    assert_empty(await finder.run(), units={"searches": 0.0})


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"first_name": "A"},
        {"first_name": "A", "last_name": "B"},
        {"first_name": "", "last_name": "B", "domain": "a.io"},
    ],
)
async def test_invalid_finder_input_is_a_bad_request_and_never_costs_a_call(
    finder: Harness, params: dict[str, Any]
) -> None:
    result = await finder.expect_failure(ErrorKind.BAD_REQUEST, params=params)
    assert "invalid find_email params" in (result.error or "")
    assert finder.router.calls.call_count == 0


# --- verify_email ------------------------------------------------------------------------------------------

VERIFY_EXPECTED = [
    ("valid", "valid", None),
    ("invalid", "invalid", None),
    ("invalid_syntax", "invalid", "failed_syntax_check"),
    ("invalid_no_mx", "invalid", "does_not_accept_mail"),
    ("accept_all", "catch_all", None),
    ("webmail", "risky", "mailbox_unchecked"),
    ("disposable", "risky", "disposable"),
    ("unknown", "unknown", None),
]


def test_every_verifier_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in VERIFY_EXPECTED} == set(VERIFIER_CASES)
    documented = {"valid", "invalid", "accept_all", "webmail", "disposable", "unknown"}  # the six in the docs
    assert documented == {VERIFIER_CASES[c]["data"]["status"] for c in VERIFIER_CASES}


@pytest.mark.parametrize(("case", "status", "sub_status"), VERIFY_EXPECTED)
async def test_verifier_statuses_map_to_the_normalised_schema(
    verifier: Harness, case: str, status: str, sub_status: str | None
) -> None:
    route = verifier.route.respond(200, json=VERIFIER_CASES[case])
    result = await verifier.run()

    assert result.ok is True and result.error_kind is None and result.error is None
    out = VerifyEmailOut.model_validate(result.data)
    assert (out.status, out.sub_status) == (status, sub_status)
    assert (out.provider, out.email, out.checked_at) == ("hunter", "patrick@stripe.com", NOW)
    assert result.found is (status != "unknown")
    # Hunter does not say whether an unknown verdict is refunded, so it is counted (the cautious side).
    assert result.units_used == {"verifications": 1.0}
    request = route.calls.last.request
    assert request.headers["X-API-KEY"] == KEY and KEY not in str(request.url)
    assert dict(request.url.params) == {"email": "patrick@stripe.com"}


@pytest.mark.parametrize(
    "body",
    [
        [],
        "just a string",
        {"data": {}},
        {"data": {"status": None}},
        {"data": {"status": 7}},
        {"data": {"status": "quantum"}},
    ],
    ids=["list", "string", "no-status", "null-status", "numeric-status", "new-status"],
)
async def test_a_verifier_body_without_a_known_status_is_unknown(verifier: Harness, body: Any) -> None:
    verifier.route.respond(200, json=body)
    await verifier.expect_failure(ErrorKind.UNKNOWN)


@pytest.mark.parametrize("bad_email", ["", "not-an-email", "a@b", 12345, None])
async def test_invalid_verifier_input_is_a_bad_request_and_never_costs_a_call(
    verifier: Harness, bad_email: Any
) -> None:
    await verifier.expect_failure(ErrorKind.BAD_REQUEST, params={"email": bad_email})
    assert verifier.router.calls.call_count == 0


async def test_a_verification_still_running_is_a_timeout_that_says_so(verifier: Harness) -> None:
    """HTTP 202: Hunter keeps working for 20 s and then asks to be polled again (counted once)."""
    verifier.route.respond(202, content=b"")
    result = await verifier.expect_failure(ErrorKind.TIMEOUT)
    assert "still in progress" in (result.error or "")


async def test_an_unexpected_smtp_answer_is_a_server_error(verifier: Harness) -> None:
    verifier.route.respond(222, content=b"")
    result = await verifier.expect_failure(ErrorKind.SERVER)
    assert "SMTP" in (result.error or "")


async def test_a_claimed_address_cannot_be_verified(verifier: Harness) -> None:
    verifier.route.respond(451, json=ERRORS["claimed_email"])
    await verifier.expect_failure(ErrorKind.EMPTY)


# --- faults, for every capability --------------------------------------------------------------------------

STATUS_EXPECTED = [
    (400, ErrorKind.BAD_REQUEST),
    (401, ErrorKind.AUTH),
    (402, ErrorKind.LIMIT_REACHED),
    (403, ErrorKind.RATE_LIMITED),  # Hunter: "403 - You have reached the rate limit"
    (408, ErrorKind.TIMEOUT),
    (422, ErrorKind.BAD_REQUEST),
    (429, ErrorKind.LIMIT_REACHED),  # Hunter: "429 - You have reached your usage limit"
    (500, ErrorKind.SERVER),
    (502, ErrorKind.SERVER),
    (503, ErrorKind.SERVER),
]


@pytest.mark.parametrize(("http_status", "kind"), STATUS_EXPECTED)
@pytest.mark.parametrize(
    "body", [b"", b"<html>nope</html>", b'{"unrelated": true}'], ids=["empty", "html", "json"]
)
async def test_http_status_maps_to_error_kind(
    h: Harness, http_status: int, kind: ErrorKind, body: bytes
) -> None:
    h.route.respond(http_status, content=body)
    result = await h.expect_failure(kind)
    assert f"HTTP {http_status}" in (result.error or "")
    assert (result.error or "").startswith("hunter: ")


async def test_404_is_empty_for_a_verification_and_an_empty_result_for_a_search(
    api: respx.MockRouter, adapter: HunterAdapter
) -> None:
    verify = harness_for("verify_email", api, adapter)
    verify.route.respond(404, content=b"")
    await verify.expect_failure(ErrorKind.EMPTY)
    find = harness_for("find_email", api, adapter)
    find.route.respond(404, content=b"")
    assert_empty(await find.run(), units={"searches": 0.0})


@pytest.mark.parametrize("status", [403, 429])
async def test_rate_and_usage_limits_carry_the_retry_after_header(h: Harness, status: int) -> None:
    kind = ErrorKind.RATE_LIMITED if status == 403 else ErrorKind.LIMIT_REACHED
    await check_retry_after_seconds(h, kind, status)


ERROR_ID_EXPECTED = [
    ("wrong_params", 400, ErrorKind.BAD_REQUEST),
    ("invalid_email", 400, ErrorKind.BAD_REQUEST),
    ("authentication_failed", 401, ErrorKind.AUTH),
    (
        "rate_limit_exceeded",
        429,
        ErrorKind.RATE_LIMITED,
    ),  # the id says rate limit although 429 usually means usage
    ("rate_limit_exceeded", 403, ErrorKind.RATE_LIMITED),
    (
        "usage_limit_reached",
        403,
        ErrorKind.LIMIT_REACHED,
    ),  # the id says usage limit although 403 usually means rate
    ("usage_limit_reached", 429, ErrorKind.LIMIT_REACHED),
    ("usage_limit_reached", 400, ErrorKind.LIMIT_REACHED),
    ("no_details", 400, ErrorKind.BAD_REQUEST),
    ("empty_errors", 401, ErrorKind.AUTH),
    # a 5xx stays a server error whatever the body claims
    ("rate_limit_exceeded", 500, ErrorKind.SERVER),
    ("usage_limit_reached", 503, ErrorKind.SERVER),
]


def test_every_error_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in ERROR_ID_EXPECTED} | {"claimed_email", "echoes_key"} == set(ERRORS)


@pytest.mark.parametrize(("case", "http_status", "kind"), ERROR_ID_EXPECTED)
async def test_error_ids_refine_the_status(h: Harness, case: str, http_status: int, kind: ErrorKind) -> None:
    h.route.respond(http_status, json=ERRORS[case])
    result = await h.expect_failure(kind)
    error = ERRORS[case]["errors"][0] if ERRORS[case]["errors"] else {}
    if error.get("id"):
        assert error["id"] in (result.error or "")
    if error.get("details"):
        assert error["details"] in (result.error or "")


async def test_a_provider_error_text_that_echoes_the_key_is_masked(h: Harness) -> None:
    body = ERRORS["echoes_key"]
    echoing = {
        "errors": [{**body["errors"][0], "details": body["errors"][0]["details"].replace("{KEY}", KEY)}]
    }
    h.route.respond(401, json=echoing)
    assert MASK in ((await h.expect_failure(ErrorKind.AUTH)).error or "")


async def test_provider_error_text_is_bounded(h: Harness) -> None:
    h.route.respond(400, json={"errors": [{"id": "wrong_params", "details": "x" * 5000}]})
    assert len((await h.expect_failure(ErrorKind.BAD_REQUEST)).error or "") < 500


@pytest.mark.parametrize("exc", TIMEOUT_EXCS, ids=exc_id)
async def test_timeouts_are_classified_not_raised(h: Harness, exc: httpx.TimeoutException) -> None:
    await check_timeout(h, exc)


@pytest.mark.parametrize("exc", TRANSPORT_EXCS, ids=exc_id)
async def test_transport_failures_are_classified_as_server_errors(
    h: Harness, exc: httpx.RequestError
) -> None:
    await check_transport_error(h, exc)


@pytest.mark.parametrize("content", MALFORMED_BODIES, ids=MALFORMED_IDS)
async def test_malformed_json_is_unknown_not_an_exception(h: Harness, content: bytes) -> None:
    await check_malformed_body(h, content)


async def test_a_redirect_is_reported_not_followed(h: Harness) -> None:
    await check_redirect_is_not_followed(h)


async def test_the_per_call_timeout_reaches_the_http_client(h: Harness) -> None:
    route = h.route.respond(200, json=OK_BODIES[h.request().capability])
    await h.run(timeout_s=25)
    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 25,
        "read": 25,
        "write": 25,
        "pool": 25,
    }


# --- connection and capability problems --------------------------------------------------------------------


async def test_missing_env_var_is_an_auth_error_that_names_it_and_makes_no_call(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HUNTER_API_KEY")
    result = await h.expect_failure(ErrorKind.AUTH)
    assert "HUNTER_API_KEY" in (result.error or "")
    assert h.router.calls.call_count == 0


async def test_an_unsupported_capability_is_a_bad_request_and_makes_no_call(h: Harness) -> None:
    result = await h.expect_failure(ErrorKind.BAD_REQUEST, capability="find_person")
    assert "find_person" in (result.error or "")
    assert h.router.calls.call_count == 0


# --- no key leaks, anywhere --------------------------------------------------------------------------------


async def test_failures_and_successes_do_not_print_or_log_the_key(
    h: Harness, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    ok = httpx.Response(200, json=OK_BODIES[h.request().capability])
    await check_no_secret_in_streams(h, capsys, caplog, ok)


# --- balance -----------------------------------------------------------------------------------------------


@pytest.fixture
def account(api: respx.MockRouter) -> respx.Route:
    return api.get(path=ACCOUNT)


def connection() -> ConnectionView:
    return ConnectionView(id="hunter-01", provider_id="hunter", auth_ref="env:HUNTER_API_KEY")


async def test_balance_reports_every_unit_the_account_has(
    adapter: HunterAdapter, account: respx.Route
) -> None:
    account.respond(200, json=ACCOUNT_CASES["ok"])
    result = await adapter.balance(connection())
    assert result.ok is True and result.error_kind is None
    assert result.units == {"credits": 9450.0, "searches": 9500.0, "verifications": 19900.0}
    assert result.remaining == 9500.0  # the primary unit is the finder's: searches
    request = account.calls.last.request
    assert request.headers["X-API-KEY"] == KEY and KEY not in str(request.url)


async def test_balance_without_a_unified_credit_bucket_has_no_credits_key(
    adapter: HunterAdapter, account: respx.Route
) -> None:
    account.respond(200, json=ACCOUNT_CASES["split_only"])
    assert (await adapter.balance(connection())).units == {"searches": 22.0, "verifications": 50.0}


@pytest.mark.parametrize("case", ["no_fields", "bad_numbers"])
async def test_balance_without_usable_numbers_is_unknown(
    adapter: HunterAdapter, account: respx.Route, case: str
) -> None:
    account.respond(200, json=ACCOUNT_CASES[case])
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind, result.remaining) == (False, ErrorKind.UNKNOWN, None)


@pytest.mark.parametrize(
    ("http_status", "kind"),
    [
        (401, ErrorKind.AUTH),
        (403, ErrorKind.RATE_LIMITED),
        (429, ErrorKind.LIMIT_REACHED),
        (500, ErrorKind.SERVER),
    ],
)
async def test_balance_http_errors_are_classified(
    adapter: HunterAdapter, account: respx.Route, http_status: int, kind: ErrorKind
) -> None:
    account.respond(http_status, headers={"Retry-After": "5"})
    result = await adapter.balance(connection())
    assert (result.ok, result.error_kind) == (False, kind)
    assert result.retry_after_s == 5.0
    assert KEY not in result.model_dump_json()


async def test_balance_timeout_and_missing_key(
    adapter: HunterAdapter, account: respx.Route, monkeypatch: pytest.MonkeyPatch
) -> None:
    account.mock(side_effect=httpx.ReadTimeout("slow"))
    assert (await adapter.balance(connection())).error_kind is ErrorKind.TIMEOUT
    monkeypatch.delenv("HUNTER_API_KEY")
    missing = await adapter.balance(connection())
    assert missing.error_kind is ErrorKind.AUTH and "HUNTER_API_KEY" in (missing.error or "")


# --- contract ----------------------------------------------------------------------------------------------


def test_adapter_is_registered_and_is_an_executor() -> None:
    assert ADAPTERS["hunter"] is HunterAdapter
    adapter = HunterAdapter(clock=lambda: NOW)
    assert isinstance(adapter, Executor)
    assert set(adapter.capabilities()) == {"find_email", "verify_email"}
