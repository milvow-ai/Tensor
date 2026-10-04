"""PageSpeed Insights adapter, fully offline (respx): field (CrUX) and lab (Lighthouse) numbers kept apart."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from functools import partial
from typing import Any

import httpx
import pytest
import respx

from farm.adapters import ADAPTERS, PageSpeedAdapter
from farm.capabilities.schemas import PageSpeedOut
from farm.executors.base import ErrorKind, Executor
from farm.secrets import MASK
from tests.adapter_testkit import (
    MALFORMED_BODIES,
    MALFORMED_IDS,
    NOW,
    TIMEOUT_EXCS,
    TRANSPORT_EXCS,
    Harness,
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

KEY = "SENTINEL-pagespeed-key-93d0c4aa"
BASE = "https://pagespeedonline.googleapis.com"
RUN = "/pagespeedonline/v5/runPagespeed"
URL = "https://example.com/"
ANALYSED = datetime(2026, 10, 4, 11, 59, 40, 394000, tzinfo=UTC)

CASES = load_cases("pagespeed", "run_pagespeed.json")
ERRORS = load_cases("pagespeed", "errors.json")


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_PAGESPEED_API_KEY", KEY)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def adapter() -> AsyncIterator[PageSpeedAdapter]:
    async with httpx.AsyncClient(verify=False) as client:
        yield PageSpeedAdapter(client, clock=lambda: NOW)


@pytest.fixture
def h(api: respx.MockRouter, adapter: PageSpeedAdapter) -> Harness:
    request = partial(
        make_request,
        provider="pagespeed",
        connection_id="pagespeed-01",
        auth_ref="env:GOOGLE_PAGESPEED_API_KEY",
        capability="pagespeed",
        params={"url": URL},
    )
    return Harness(adapter=adapter, router=api, route=api.get(path=RUN), request=request, secrets=(KEY,))


# --- happy path --------------------------------------------------------------------------------------------


async def test_page_level_result_keeps_field_and_lab_data_apart(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["page_level"])
    result = await h.run()

    assert result.ok is True and result.found is True and result.error_kind is None and result.error is None
    assert result.units_used == {"requests": 1.0} and result.cost_usd == 0
    out = PageSpeedOut.model_validate(result.data)
    assert (out.source.provider, out.source.connection_id, out.observed_at) == (
        "pagespeed",
        "pagespeed-01",
        NOW,
    )
    assert (out.url, out.final_url, out.strategy) == (URL, "https://www.example.com/", "mobile")

    field = out.field
    assert field is not None and field.kind == "field" and field.scope == "page"
    assert (field.lcp_ms, field.inp_ms, field.cls) == (2400.0, 180.0, 0.07)  # CLS arrives x100
    assert (field.lcp_rating, field.inp_rating, field.cls_rating) == ("needs_improvement", "good", "good")
    assert field.as_of == ANALYSED

    lab = out.lab
    assert lab.kind == "lab" and lab.performance_score == 96
    assert (lab.lcp_ms, lab.cls, lab.tbt_ms, lab.fcp_ms, lab.speed_index_ms) == (
        2874.1,
        0.012,
        120.0,
        1432.5,
        1650.3,
    )
    assert lab.lighthouse_version == "12.6.0"
    # Field and lab numbers differ and must never be merged: the lab LCP is not the user-experienced LCP.
    assert field.lcp_ms != lab.lcp_ms

    query = route.calls.last.request.url.params
    assert dict(query) == {"url": URL, "strategy": "mobile", "key": KEY}


async def test_strategy_is_passed_through(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["page_level"])
    out = PageSpeedOut.model_validate((await h.run(params={"url": URL, "strategy": "desktop"})).data)
    assert out.strategy == "desktop"
    assert route.calls.last.request.url.params["strategy"] == "desktop"


async def test_origin_fallback_is_reported_as_origin_scope(h: Harness) -> None:
    h.route.respond(200, json=CASES["origin_fallback"])
    out = PageSpeedOut.model_validate((await h.run()).data)
    assert out.field is not None and out.field.scope == "origin"
    assert (out.field.lcp_ms, out.field.inp_ms, out.field.cls) == (4100.0, None, 0.31)
    assert out.field.cls_rating == "poor"
    assert (out.lab.performance_score, out.lab.lcp_ms) == (41, 5100.0)


async def test_origin_data_is_used_when_the_page_has_none(h: Harness) -> None:
    h.route.respond(200, json=CASES["origin_only"])
    out = PageSpeedOut.model_validate((await h.run()).data)
    assert out.field is not None and out.field.scope == "origin"
    assert (out.field.inp_ms, out.field.inp_rating, out.field.lcp_ms) == (640.0, "poor", None)


async def test_a_page_without_crux_data_still_has_lab_data(h: Harness) -> None:
    h.route.respond(200, json=CASES["no_crux"])
    result = await h.run()
    assert result.ok is True and result.found is True
    out = PageSpeedOut.model_validate(result.data)
    assert out.field is None
    assert (out.lab.performance_score, out.lab.fcp_ms, out.lab.cls) == (100, 900.0, 0.0)
    assert out.lab.lcp_ms is None


async def test_a_null_score_with_other_metrics_is_kept_as_none(h: Harness) -> None:
    body = {
        **CASES["no_crux"],
        "lighthouseResult": {
            "audits": {"speed-index": {"numericValue": 2000}},
            "categories": {"performance": {"score": None}},
        },
    }
    h.route.respond(200, json=body)
    lab = PageSpeedOut.model_validate((await h.run()).data).lab
    assert (lab.performance_score, lab.speed_index_ms) == (None, 2000.0)


@pytest.mark.parametrize("case", ["null_score", "no_lighthouse_metrics", "no_lighthouse_result"])
async def test_a_response_without_lighthouse_numbers_is_unknown(h: Harness, case: str) -> None:
    h.route.respond(200, json=CASES[case])
    await h.expect_failure(ErrorKind.UNKNOWN)


@pytest.mark.parametrize(
    ("case", "kind"),
    [("runtime_error_dns", ErrorKind.BAD_REQUEST), ("runtime_error_timeout", ErrorKind.TIMEOUT)],
)
async def test_a_lighthouse_runtime_error_is_a_failure_that_names_the_code(
    h: Harness, case: str, kind: ErrorKind
) -> None:
    h.route.respond(200, json=CASES[case])
    result = await h.expect_failure(kind)
    code = CASES[case]["lighthouseResult"]["runtimeError"]["code"]
    assert code in (result.error or "")


def test_every_fixture_case_is_exercised() -> None:
    exercised = {
        "page_level",
        "origin_fallback",
        "origin_only",
        "no_crux",
        "null_score",
        "no_lighthouse_metrics",
        "no_lighthouse_result",
        "runtime_error_dns",
        "runtime_error_timeout",
    }
    assert exercised == set(CASES)


# --- input -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"url": ""},
        {"url": "example.com"},
        {"url": "ftp://example.com/"},
        {"url": "javascript:alert(1)"},
        {"url": "https://user:pass@example.com/"},
        {"url": "https://example.com/" + "a" * 2100},
        {"url": URL, "strategy": "tablet"},
        {"url": URL, "category": "seo"},
    ],
)
async def test_invalid_input_is_a_bad_request_and_never_costs_a_call(
    h: Harness, params: dict[str, Any]
) -> None:
    result = await h.expect_failure(ErrorKind.BAD_REQUEST, params=params)
    assert "invalid pagespeed params" in (result.error or "")
    assert h.router.calls.call_count == 0


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
    assert (result.error or "").startswith("pagespeed: ")


async def test_429_carries_retry_after_seconds(h: Harness) -> None:
    await check_retry_after_seconds(h, ErrorKind.RATE_LIMITED, 429)


ERROR_EXPECTED = [
    ("api_key_invalid", 400, ErrorKind.AUTH),
    ("permission_denied", 403, ErrorKind.AUTH),
    ("quota_daily", 429, ErrorKind.LIMIT_REACHED),  # the daily quota: do not retry until it resets
    ("quota_per_minute", 429, ErrorKind.RATE_LIMITED),
    ("daily_limit_legacy", 403, ErrorKind.LIMIT_REACHED),
    ("rate_limit_legacy", 403, ErrorKind.RATE_LIMITED),
    ("invalid_url", 400, ErrorKind.BAD_REQUEST),
    (
        "lighthouse_dns",
        500,
        ErrorKind.BAD_REQUEST,
    ),  # the target page's fault: retrying the same URL cannot help
    ("lighthouse_not_html", 500, ErrorKind.BAD_REQUEST),
    ("lighthouse_other", 500, ErrorKind.SERVER),
]


def test_every_error_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in ERROR_EXPECTED} | {"echoes_key"} == set(ERRORS)


@pytest.mark.parametrize(("case", "http_status", "kind"), ERROR_EXPECTED)
async def test_google_error_envelopes_are_classified(
    h: Harness, case: str, http_status: int, kind: ErrorKind
) -> None:
    h.route.respond(http_status, json=ERRORS[case])
    result = await h.expect_failure(kind)
    assert ERRORS[case]["error"]["message"][:40] in (result.error or "")


async def test_a_provider_error_text_that_echoes_the_key_is_masked(h: Harness) -> None:
    body = ERRORS["echoes_key"]
    h.route.respond(
        400, json={"error": {**body["error"], "message": body["error"]["message"].replace("{KEY}", KEY)}}
    )
    assert MASK in ((await h.expect_failure(ErrorKind.AUTH)).error or "")


async def test_provider_error_text_is_bounded(h: Harness) -> None:
    h.route.respond(400, json={"error": {"code": 400, "message": "x" * 5000}})
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


@pytest.mark.parametrize("body", [[], "just a string", 42], ids=["list", "string", "number"])
async def test_json_of_the_wrong_shape_is_unknown(h: Harness, body: Any) -> None:
    h.route.respond(200, json=body)
    await h.expect_failure(ErrorKind.UNKNOWN)


async def test_a_redirect_is_reported_not_followed(h: Harness) -> None:
    await check_redirect_is_not_followed(h)


async def test_a_long_analysis_gets_the_callers_timeout(h: Harness) -> None:
    route = h.route.respond(200, json=CASES["page_level"])
    await h.run(timeout_s=90)
    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 90,
        "read": 90,
        "write": 90,
        "pool": 90,
    }


async def test_missing_env_var_is_an_auth_error_that_names_it_and_makes_no_call(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GOOGLE_PAGESPEED_API_KEY")
    result = await h.expect_failure(ErrorKind.AUTH)
    assert "GOOGLE_PAGESPEED_API_KEY" in (result.error or "")
    assert h.router.calls.call_count == 0


async def test_an_unsupported_capability_is_a_bad_request(h: Harness) -> None:
    await h.expect_failure(ErrorKind.BAD_REQUEST, capability="verify_email")
    assert h.router.calls.call_count == 0


async def test_failures_and_successes_do_not_print_or_log_the_key(
    h: Harness, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    await check_no_secret_in_streams(h, capsys, caplog, httpx.Response(200, json=CASES["page_level"]))


async def test_the_key_in_the_query_string_is_masked_in_httpx_log_lines(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="httpx")
    h.route.respond(200, json=CASES["page_level"])
    await h.run()
    assert "HTTP Request: GET" in caplog.text  # PageSpeed authenticates with key=..., so the URL is the risk
    assert KEY not in caplog.text and f"key={MASK}" in caplog.text


def test_adapter_is_registered_and_is_an_executor() -> None:
    assert ADAPTERS["pagespeed"] is PageSpeedAdapter
    adapter = PageSpeedAdapter(clock=lambda: NOW)
    assert isinstance(adapter, Executor)
    assert set(adapter.capabilities()) == {"pagespeed"}
