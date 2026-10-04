"""Adzuna adapter, fully offline (respx): jobs_lookup by keywords and place.

Adzuna needs two credentials (app id + app key). Both are references resolved at call time; the tests use two
different sentinels and assert that neither appears anywhere.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from functools import partial
from typing import Any

import httpx
import pytest
import respx

from farm.adapters import ADAPTERS, AdzunaAdapter
from farm.capabilities.schemas import JobsLookupOut
from farm.executors.base import ErrorKind, Executor
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

KEY = "SENTINEL-adzuna-appkey-6e2f10bd"
APP_ID = "SENTINEL-adzuna-appid-a91c33f0"
BASE = "https://api.adzuna.com"
SEARCH_GB = "/v1/api/jobs/gb/search/1"
SEARCH_US = "/v1/api/jobs/us/search/1"

CASES = load_cases("adzuna", "search.json")
ERRORS = load_cases("adzuna", "errors.json")
META = {"app_id_ref": "env:ADZUNA_APP_ID"}


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_KEY", KEY)
    monkeypatch.setenv("ADZUNA_APP_ID", APP_ID)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def adapter() -> AsyncIterator[AdzunaAdapter]:
    async with httpx.AsyncClient(verify=False) as client:
        yield AdzunaAdapter(client, clock=lambda: NOW)


@pytest.fixture
def h(api: respx.MockRouter, adapter: AdzunaAdapter) -> Harness:
    request = partial(
        make_request,
        provider="adzuna",
        connection_id="adzuna-01",
        auth_ref="env:ADZUNA_APP_KEY",
        capability="jobs_lookup",
        params={"what": "javascript developer", "where": "london", "country": "gb"},
        meta=META,
    )
    return Harness(
        adapter=adapter, router=api, route=api.get(path=SEARCH_GB), request=request, secrets=(KEY, APP_ID)
    )


# --- happy path --------------------------------------------------------------------------------------------


async def test_search_maps_postings_and_charges_one_request(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["ok"])
    result = await h.run()

    assert result.ok is True and result.found is True and result.error_kind is None and result.error is None
    assert result.units_used == {"requests": 1.0} and result.cost_usd == 0
    out = JobsLookupOut.model_validate(result.data)
    assert (out.source.provider, out.source.connection_id, out.observed_at) == ("adzuna", "adzuna-01", NOW)
    assert (out.platform, out.total, out.board, out.board_guessed) == ("adzuna", 1234, None, False)
    first, second = out.postings
    assert (first.id, first.title, first.company) == (
        "129698749",
        "Javascript Developer",
        "Corporate Project Solutions",
    )
    assert (first.location, first.department, first.employment_type) == (
        "Marlow, Buckinghamshire",
        "IT Jobs",
        "permanent",
    )
    assert first.url == "http://adzuna.co.uk/jobs/land/ad/129698749" and first.platform == "adzuna"
    assert first.posted_at == datetime(2013, 11, 8, 18, 7, 39, tzinfo=UTC)
    assert (first.salary_min, first.salary_max) == (50000.0, 55000.0)
    assert second.employment_type == "full_time"  # contract_time wins over contract_type

    request = route.calls.last.request
    assert request.headers["accept"] == "application/json"  # without it Adzuna answers JSONP
    assert dict(request.url.params) == {
        "app_id": APP_ID,
        "app_key": KEY,
        "results_per_page": "20",
        "what": "javascript developer",
        "where": "london",
    }


async def test_country_is_normalised_and_selects_the_endpoint(api: respx.MockRouter, h: Harness) -> None:
    us = api.get(path=SEARCH_US).respond(200, json=CASES["ok"])
    await h.run(params={"what": "nurse", "country": " US "})
    assert us.called and not h.route.called
    await h.run(params={"what": "nurse"})  # the default country is us
    assert us.call_count == 2


async def test_where_alone_is_a_search_too(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["ok"])
    await h.run(params={"where": "london", "country": "gb"})
    assert "what" not in route.calls.last.request.url.params


async def test_the_limit_sets_the_page_size_and_truncates(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["ok"])
    out = JobsLookupOut.model_validate(
        (await h.run(params={"what": "dev", "country": "gb", "limit": 1})).data
    )
    assert route.calls.last.request.url.params["results_per_page"] == "1"
    assert len(out.postings) == 1 and out.total == 1234


async def test_a_predicted_salary_is_not_an_employers_figure_and_is_dropped(h: Harness) -> None:
    h.route.respond(200, json=CASES["predicted_salary"])
    posting = JobsLookupOut.model_validate((await h.run()).data).postings[0]
    assert (posting.salary_min, posting.salary_max) == (None, None)
    assert posting.id == "987654" and posting.company == "Acme Corp"  # a numeric id becomes text


async def test_a_company_name_searches_as_keywords_and_keeps_only_that_company(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["mixed_companies"])
    result = await h.run(params={"company": "acme", "country": "gb"})
    assert route.calls.last.request.url.params["what"] == "acme"
    out = JobsLookupOut.model_validate(result.data)
    assert [p.company for p in out.postings] == [
        "ACME Corporation",
        "Acmeless Ltd",
    ]  # case-insensitive containment


async def test_what_wins_over_company_as_the_keywords_and_no_company_filter_applies(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["mixed_companies"])
    out = JobsLookupOut.model_validate(
        (await h.run(params={"what": "engineer", "company": "acme", "country": "gb"})).data
    )
    assert route.calls.last.request.url.params["what"] == "engineer"
    assert len(out.postings) == 3


async def test_a_company_filter_that_removes_everything_is_an_empty_result(h: Harness) -> None:
    h.route.respond(200, json=CASES["mixed_companies"])
    assert_empty(await h.run(params={"company": "initech", "country": "gb"}), units={"requests": 1.0})


@pytest.mark.parametrize("case", ["no_results", "unusable_items"])
async def test_nothing_usable_is_an_empty_result_and_the_request_is_still_counted(
    h: Harness, case: str
) -> None:
    h.route.respond(200, json=CASES[case])
    result = await h.run()
    assert_empty(result, units={"requests": 1.0})  # Adzuna counts every hit against the quota
    assert JobsLookupOut.model_validate(result.data).postings == []


async def test_an_answer_without_a_results_list_is_malformed_not_empty(h: Harness) -> None:
    h.route.respond(200, json=CASES["no_results_key"])
    await h.expect_failure(ErrorKind.UNKNOWN)


# --- input -------------------------------------------------------------------------------------------------


async def test_a_board_token_alone_cannot_be_served_and_makes_no_call(h: Harness) -> None:
    result = await h.expect_failure(ErrorKind.BAD_REQUEST, params={"board": "stripe"})
    assert "cannot read a job board" in (result.error or "")
    assert h.router.calls.call_count == 0


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"country": "gb"},
        {"what": "dev", "country": "usa"},
        {"what": "dev", "country": "1a"},
        {"what": "dev", "limit": 0},
        {"what": "dev", "limit": 51},
        {"what": ""},
        {"what": "dev", "ats": "lever"},
        {"what": "dev", "surprise": True},
        {"what": "x" * 201},
    ],
)
async def test_invalid_input_is_a_bad_request_and_never_costs_a_call(
    h: Harness, params: dict[str, Any]
) -> None:
    result = await h.expect_failure(ErrorKind.BAD_REQUEST, params=params)
    assert "invalid jobs_lookup params" in (result.error or "")
    assert h.router.calls.call_count == 0


# --- credentials: two secrets, both by reference -----------------------------------------------------------


async def test_a_connection_without_an_app_id_reference_is_an_auth_error_and_makes_no_call(
    h: Harness,
) -> None:
    result = await h.expect_failure(ErrorKind.AUTH, meta={})
    assert "app_id_ref" in (result.error or "")
    assert h.router.calls.call_count == 0


async def test_an_unset_app_id_variable_is_an_auth_error_that_names_it(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID")
    result = await h.expect_failure(ErrorKind.AUTH)
    assert "ADZUNA_APP_ID" in (result.error or "")
    assert h.router.calls.call_count == 0


async def test_an_unset_key_variable_is_an_auth_error_that_names_it(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ADZUNA_APP_KEY")
    result = await h.expect_failure(ErrorKind.AUTH)
    assert "ADZUNA_APP_KEY" in (result.error or "")
    assert h.router.calls.call_count == 0


async def test_a_pasted_value_instead_of_a_reference_is_never_echoed(h: Harness) -> None:
    result = await h.expect_failure(ErrorKind.AUTH, meta={"app_id_ref": "PASTED-APP-ID-0123456789"})
    assert "PASTED-APP-ID" not in (result.error or "")


async def test_both_credentials_are_masked_in_httpx_log_lines(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="httpx")
    h.route.respond(200, json=CASES["ok"])
    await h.run()
    assert "HTTP Request: GET" in caplog.text
    assert KEY not in caplog.text and APP_ID not in caplog.text and MASK in caplog.text


# --- faults ------------------------------------------------------------------------------------------------

STATUS_EXPECTED = [
    (400, ErrorKind.BAD_REQUEST),
    (401, ErrorKind.AUTH),
    (402, ErrorKind.LIMIT_REACHED),
    (403, ErrorKind.AUTH),
    (404, ErrorKind.EMPTY),
    (408, ErrorKind.TIMEOUT),
    (429, ErrorKind.RATE_LIMITED),
    (500, ErrorKind.SERVER),
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
    assert (result.error or "").startswith("adzuna: ")


async def test_429_carries_retry_after_seconds(h: Harness) -> None:
    await check_retry_after_seconds(h, ErrorKind.RATE_LIMITED, 429)


ERROR_EXPECTED = [
    ("auth_fail", 401, ErrorKind.AUTH),
    ("auth_fail", 403, ErrorKind.AUTH),
    ("auth_fail", 400, ErrorKind.AUTH),  # the exception code decides, whatever the status
    ("daily_limit", 429, ErrorKind.LIMIT_REACHED),  # "250 hits per day": do not retry in a minute
    ("minute_limit", 429, ErrorKind.RATE_LIMITED),
    ("bad_country", 400, ErrorKind.BAD_REQUEST),
    ("bad_country", 404, ErrorKind.EMPTY),
    ("no_exception", 422, ErrorKind.BAD_REQUEST),
    # a 5xx stays a server error whatever the body says
    ("auth_fail", 500, ErrorKind.SERVER),
    ("daily_limit", 503, ErrorKind.SERVER),
]


def test_every_error_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in ERROR_EXPECTED} | {"echoes_key"} == set(ERRORS)


@pytest.mark.parametrize(("case", "http_status", "kind"), ERROR_EXPECTED)
async def test_error_bodies_are_classified_from_status_then_code_then_words(
    h: Harness, case: str, http_status: int, kind: ErrorKind
) -> None:
    h.route.respond(http_status, json=ERRORS[case])
    result = await h.expect_failure(kind)
    code = ERRORS[case].get("exception")
    if code:
        assert code in (result.error or "")


async def test_a_provider_error_text_that_echoes_the_key_is_masked(h: Harness) -> None:
    body = {**ERRORS["echoes_key"], "description": ERRORS["echoes_key"]["description"].replace("{KEY}", KEY)}
    h.route.respond(401, json=body)
    assert MASK in ((await h.expect_failure(ErrorKind.AUTH)).error or "")


async def test_provider_error_text_is_bounded(h: Harness) -> None:
    h.route.respond(400, json={"exception": "X", "description": "x" * 5000})
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


@pytest.mark.parametrize("body", [[], "just a string", 42, {"results": "nope"}], ids=str)
async def test_json_of_the_wrong_shape_is_unknown(h: Harness, body: Any) -> None:
    h.route.respond(200, json=body)
    await h.expect_failure(ErrorKind.UNKNOWN)


async def test_a_redirect_is_reported_not_followed(h: Harness) -> None:
    await check_redirect_is_not_followed(h)


async def test_an_unsupported_capability_is_a_bad_request(h: Harness) -> None:
    await h.expect_failure(ErrorKind.BAD_REQUEST, capability="find_person")
    assert h.router.calls.call_count == 0


async def test_failures_and_successes_do_not_print_or_log_either_credential(
    h: Harness, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    await check_no_secret_in_streams(h, capsys, caplog, httpx.Response(200, json=CASES["ok"]))


def test_adapter_is_registered_and_is_an_executor() -> None:
    assert ADAPTERS["adzuna"] is AdzunaAdapter
    adapter = AdzunaAdapter(clock=lambda: NOW)
    assert isinstance(adapter, Executor)
    assert set(adapter.capabilities()) == {"jobs_lookup"}
