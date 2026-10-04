"""Apollo adapter, fully offline (respx): find_person (0 credits), enrich_company (1), find_email.

The recurring assertion: nothing the adapter returns, raises, logs or prints contains the API key.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from functools import partial
from typing import Any

import httpx
import pytest
import respx

from farm.adapters import ADAPTERS, ApolloAdapter
from farm.capabilities.schemas import EnrichCompanyOut, FindEmailOut, FindPersonOut
from farm.executors.base import ConnectionView, ErrorKind, ExecResult, Executor
from farm.secrets import MASK
from tests.adapter_testkit import (
    MALFORMED_BODIES,
    MALFORMED_IDS,
    NOW,
    TIMEOUT_EXCS,
    TRANSPORT_EXCS,
    Harness,
    assert_empty,
    assert_failed,
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

KEY = "SENTINEL-apollo-key-5c19e7d2"
BASE = "https://api.apollo.io"
SEARCH = "/api/v1/mixed_people/api_search"
ENRICH = "/api/v1/organizations/enrich"
MATCH = "/api/v1/people/match"

SEARCH_CASES = load_cases("apollo", "people_search.json")
ORG_CASES = load_cases("apollo", "org_enrich.json")
MATCH_CASES = load_cases("apollo", "people_match.json")
ERRORS = load_cases("apollo", "errors.json")

DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "find_person": {"company_domain": "acme.com"},
    "enrich_company": {"domain": "acme.com"},
    "find_email": {"first_name": "Jordan", "last_name": "Blake", "domain": "northstaranalytics.io"},
}
ROUTES = {"find_person": ("POST", SEARCH), "enrich_company": ("GET", ENRICH), "find_email": ("POST", MATCH)}
OK_BODIES = {
    "find_person": SEARCH_CASES["ok"],
    "enrich_company": ORG_CASES["apollo_io"],
    "find_email": MATCH_CASES["verified"],
}


@pytest.fixture(autouse=True)
def _apollo_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APOLLO_API_KEY", KEY)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
async def adapter() -> AsyncIterator[ApolloAdapter]:
    async with httpx.AsyncClient(verify=False) as client:
        yield ApolloAdapter(client, clock=lambda: NOW)


def harness_for(capability: str, api: respx.MockRouter, adapter: ApolloAdapter) -> Harness:
    method, path = ROUTES[capability]
    route = api.route(method=method, path=path)
    request = partial(
        make_request,
        provider="apollo",
        connection_id="apollo-01",
        auth_ref="env:APOLLO_API_KEY",
        capability=capability,
        params=DEFAULT_PARAMS[capability],
    )
    return Harness(adapter=adapter, router=api, route=route, request=request, secrets=(KEY,))


@pytest.fixture(params=list(ROUTES))
def h(request: pytest.FixtureRequest, api: respx.MockRouter, adapter: ApolloAdapter) -> Harness:
    """The fault battery below runs once per capability."""
    return harness_for(str(request.param), api, adapter)


@pytest.fixture
def search(api: respx.MockRouter, adapter: ApolloAdapter) -> Harness:
    return harness_for("find_person", api, adapter)


@pytest.fixture
def enrich(api: respx.MockRouter, adapter: ApolloAdapter) -> Harness:
    return harness_for("enrich_company", api, adapter)


@pytest.fixture
def match(api: respx.MockRouter, adapter: ApolloAdapter) -> Harness:
    return harness_for("find_email", api, adapter)


# --- find_person: 0 credits, masked surnames ----------------------------------------------------------------


async def test_people_search_maps_people_and_costs_nothing(search: Harness) -> None:
    search.route.respond(200, json=SEARCH_CASES["ok"])
    result = await search.run()

    assert result.ok is True and result.found is True and result.error_kind is None and result.error is None
    assert result.units_used == {"credits": 0.0}  # people search is documented as 0 credits
    assert result.cost_usd == 0
    out = FindPersonOut.model_validate(result.data)
    assert (out.source.provider, out.source.connection_id, out.observed_at) == ("apollo", "apollo-01", NOW)
    assert out.total == 232764882 and len(out.people) == 3
    first = out.people[0]
    assert first.provider_person_id == "67bdafd0c3a4c50001bbd7c2"
    assert (first.name, first.first_name, first.last_name) == ("Andrew Hu***n", "Andrew", "Hu***n")
    assert first.name_is_partial is True  # the free search masks the surname
    assert first.title == "Professor and Neuroscientist at Stanford & Host"
    assert first.company == "Scicomm Media" and first.has_email is True and first.linkedin is None
    assert out.people[2].has_email is False


async def test_people_search_request_shape_and_credentials(search: Harness) -> None:
    route = search.route.respond(200, json=SEARCH_CASES["ok"])
    await search.run(
        params={
            "company_domain": "https://www.Acme.com/about",
            "titles": ["Head of Marketing", "VP Marketing"],
            "seniority": ["vp", "head"],
            "limit": 25,
        }
    )
    request = route.calls.last.request
    assert request.method == "POST" and request.content == b""
    assert request.headers["x-api-key"] == KEY  # the key is a header, never part of the URL
    assert KEY not in str(request.url)
    query = request.url.params
    assert query.get_list("q_organization_domains_list[]") == ["acme.com"]  # URL reduced to the bare domain
    assert query.get_list("person_titles[]") == ["Head of Marketing", "VP Marketing"]
    assert query.get_list("person_seniorities[]") == ["vp", "head"]
    assert (query["per_page"], query["page"]) == ("25", "1")


async def test_people_search_leaves_unused_filters_out(search: Harness) -> None:
    route = search.route.respond(200, json=SEARCH_CASES["ok"])
    await search.run()
    query = route.calls.last.request.url.params
    assert "person_titles[]" not in query and "person_seniorities[]" not in query


async def test_a_full_name_is_not_flagged_partial_and_a_nameless_person_is_dropped(search: Harness) -> None:
    search.route.respond(200, json=SEARCH_CASES["masked_and_full"])
    out = FindPersonOut.model_validate((await search.run()).data)
    jordan, casey = out.people
    assert (jordan.name, jordan.name_is_partial) == ("Jordan Blake", False)
    assert jordan.linkedin == "http://www.linkedin.com/in/jordan-blake-4a7c21"
    assert (casey.name, casey.name_is_partial) == ("Casey Morgan", False)  # the provider's own full name wins

    search.route.respond(200, json=SEARCH_CASES["no_name"])
    out = FindPersonOut.model_validate((await search.run()).data)
    assert [p.name for p in out.people] == ["Sam Li***u"]  # b1 has no name at all: nothing to show


async def test_people_search_without_results_is_an_empty_result_that_is_still_free(search: Harness) -> None:
    search.route.respond(200, json=SEARCH_CASES["empty"])
    result = await search.run()
    assert_empty(result, units={"credits": 0.0})
    assert FindPersonOut.model_validate(result.data).people == []
    assert "acme.com" in (result.error or "")


async def test_people_search_by_company_name_alone_is_not_supported_and_makes_no_call(
    search: Harness,
) -> None:
    result = await search.expect_failure(ErrorKind.BAD_REQUEST, params={"company_name": "Acme"})
    assert "company domain" in (result.error or "")
    assert search.router.calls.call_count == 0


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"titles": ["CEO"]},
        {"company_domain": "not a domain"},
        {"company_domain": "acme.com", "limit": 0},
        {"company_domain": "acme.com", "limit": 101},
        {"company_domain": "acme.com", "seniority": ["emperor"]},
        {"company_domain": "acme.com", "titles": ["x"] * 21},
        {"company_domain": "acme.com", "surprise": 1},
        {"company_domain": "someone@acme.com"},
        {"company_domain": "acme.com:8080"},
    ],
)
async def test_invalid_search_input_is_a_bad_request_and_never_costs_a_call(
    search: Harness, params: dict[str, Any]
) -> None:
    result = await search.expect_failure(ErrorKind.BAD_REQUEST, params=params)
    assert "invalid find_person params" in (result.error or "")
    assert search.router.calls.call_count == 0


# --- enrich_company: 1 credit per organization -------------------------------------------------------------


async def test_organization_enrichment_maps_the_profile_and_charges_one_credit(enrich: Harness) -> None:
    route = enrich.route.respond(200, json=ORG_CASES["apollo_io"])
    result = await enrich.run(params={"domain": "Apollo.io", "name": "Apollo"})

    assert result.ok is True and result.found is True
    assert result.units_used == {"credits": 1.0}
    out = EnrichCompanyOut.model_validate(result.data)
    assert (out.source.provider, out.source.connection_id) == ("apollo", "apollo-01")
    assert (out.domain, out.name, out.industry) == (
        "apollo.io",
        "Apollo.io",
        "information technology & services",
    )
    assert (out.employee_count, out.size_range, out.founded_year) == (1600, "1001-5000", 2015)
    assert (out.location.city, out.location.region, out.location.country) == (
        "San Francisco",
        "California",
        "United States",
    )
    assert out.socials.linkedin == "https://www.linkedin.com/company/apolloio"
    assert out.socials.twitter == "https://x.com/useapolloio"
    assert out.socials.facebook == "https://www.facebook.com/example-company"
    assert out.tech_hints[:3] == ["AI", "Android", "Salesforce"] and "Stripe" in out.tech_hints
    assert out.website == "http://www.apollo.io" and (out.description or "").startswith("Apollo.io combines")

    request = route.calls.last.request
    assert request.method == "GET" and request.headers["x-api-key"] == KEY and KEY not in str(request.url)
    assert dict(request.url.params) == {"domain": "apollo.io", "name": "Apollo"}


async def test_technologies_fall_back_to_the_detailed_list(enrich: Harness) -> None:
    enrich.route.respond(200, json=ORG_CASES["no_technology_names"])
    out = EnrichCompanyOut.model_validate((await enrich.run()).data)
    assert out.tech_hints == ["WordPress.org", "reCAPTCHA"]
    assert (out.employee_count, out.size_range) == (42, "11-50")
    assert out.domain == "northstaranalytics.io"  # Apollo's primary domain wins over the one asked for


@pytest.mark.parametrize("case", ["no_org_key", "null_org", "empty_org"])
async def test_an_unknown_organization_is_an_empty_result_and_costs_nothing(
    enrich: Harness, case: str
) -> None:
    enrich.route.respond(200, json=ORG_CASES[case])
    result = await enrich.run()
    assert_empty(result, units={"credits": 0.0})
    assert EnrichCompanyOut.model_validate(result.data).domain == "acme.com"


@pytest.mark.parametrize(
    "params", [{}, {"domain": ""}, {"domain": "acme.com", "name": ""}, {"url": "acme.com"}]
)
async def test_invalid_enrichment_input_is_a_bad_request(enrich: Harness, params: dict[str, Any]) -> None:
    result = await enrich.expect_failure(ErrorKind.BAD_REQUEST, params=params)
    assert "invalid enrich_company params" in (result.error or "")
    assert enrich.router.calls.call_count == 0


async def test_a_non_object_enrichment_body_is_unknown(enrich: Harness) -> None:
    enrich.route.respond(200, json=["not", "an", "object"])
    await enrich.expect_failure(ErrorKind.UNKNOWN)


# --- find_email: people match, 1 credit when a person is matched -------------------------------------------


async def test_people_match_returns_a_verified_email_and_charges_one_credit(match: Harness) -> None:
    route = match.route.respond(200, json=MATCH_CASES["verified"])
    result = await match.run()

    assert result.ok is True and result.found is True and result.units_used == {"credits": 1.0}
    out = FindEmailOut.model_validate(result.data)
    assert out.email == "jordan.blake@northstaranalytics.io"
    assert (out.verification, out.verification_detail, out.confidence) == ("valid", "verified", None)
    assert (out.position, out.linkedin) == ("Founder & CEO", "http://www.linkedin.com/in/jordan-blake-4a7c21")
    assert (out.first_name, out.last_name, out.domain) == ("Jordan", "Blake", "northstaranalytics.io")
    assert out.source.connection_id == "apollo-01"

    request = route.calls.last.request
    assert request.method == "POST" and request.headers["x-api-key"] == KEY and KEY not in str(request.url)
    # No reveal_* / waterfall flags: those are what turn a 1-credit match into a 9+ credit one.
    assert dict(request.url.params) == {
        "first_name": "Jordan",
        "last_name": "Blake",
        "domain": "northstaranalytics.io",
    }


async def test_an_extrapolated_email_is_not_called_verified(match: Harness) -> None:
    match.route.respond(200, json=MATCH_CASES["extrapolated_email"])
    out = FindEmailOut.model_validate((await match.run()).data)
    assert (out.verification, out.verification_detail, out.confidence) == ("unknown", "likely to engage", 87)


@pytest.mark.parametrize("case", ["locked_email_placeholder", "no_email"])
async def test_a_match_without_a_usable_email_is_empty_but_the_credit_is_still_charged(
    match: Harness, case: str
) -> None:
    """Apollo bills a matched person's demographics even without an email, so the ledger must see 1."""
    match.route.respond(200, json=MATCH_CASES[case])
    result = await match.run()
    assert_empty(result, units={"credits": 1.0})
    out = FindEmailOut.model_validate(result.data)
    assert out.email is None and out.verification is None


@pytest.mark.parametrize("case", ["match_none", "empty_person"])
async def test_no_match_is_empty_and_free(match: Harness, case: str) -> None:
    match.route.respond(200, json=MATCH_CASES[case])
    assert_empty(await match.run(), units={"credits": 0.0})


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"first_name": "Jordan"},
        {"first_name": "Jordan", "last_name": "Blake"},
        {"first_name": "", "last_name": "B", "domain": "a.io"},
    ],
)
async def test_invalid_match_input_is_a_bad_request(match: Harness, params: dict[str, Any]) -> None:
    await match.expect_failure(ErrorKind.BAD_REQUEST, params=params)
    assert match.router.calls.call_count == 0


# --- faults, for every capability --------------------------------------------------------------------------

STATUS_EXPECTED = [
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
]


@pytest.mark.parametrize(("http_status", "kind"), STATUS_EXPECTED)
@pytest.mark.parametrize(
    "body", [b"", b"Invalid API key.", b'{"unrelated": true}'], ids=["empty", "text", "json"]
)
async def test_http_status_maps_to_error_kind(
    h: Harness, http_status: int, kind: ErrorKind, body: bytes
) -> None:
    h.route.respond(http_status, content=body)
    result = await h.expect_failure(kind)
    assert f"HTTP {http_status}" in (result.error or "")
    assert (result.error or "").startswith("apollo: ")


async def test_429_carries_the_retry_after_header(h: Harness) -> None:
    await check_retry_after_seconds(h, ErrorKind.RATE_LIMITED, 429)


async def test_429_without_a_header_uses_apollos_own_retry_hint(h: Harness) -> None:
    h.route.respond(429, json=ERRORS["rate_limit"])
    result = await h.expect_failure(ErrorKind.RATE_LIMITED)
    assert result.retry_after_s == 41.0
    assert "API_RATE_LIMIT_EXCEEDED" in (result.error or "")


async def test_the_retry_after_header_beats_the_body_hint(h: Harness) -> None:
    h.route.respond(429, json=ERRORS["rate_limit"], headers={"Retry-After": "7"})
    assert (await h.expect_failure(ErrorKind.RATE_LIMITED)).retry_after_s == 7.0


ERROR_BODY_EXPECTED = [
    ("forbidden_scope", 403, ErrorKind.AUTH),
    ("forbidden_scope", 401, ErrorKind.AUTH),
    ("validation_org", 422, ErrorKind.BAD_REQUEST),
    ("validation_org", 400, ErrorKind.BAD_REQUEST),
    ("out_of_credits_code", 402, ErrorKind.LIMIT_REACHED),
    ("out_of_credits_code", 403, ErrorKind.LIMIT_REACHED),  # the credit code beats the generic 403 = AUTH
    ("out_of_credits_code", 422, ErrorKind.LIMIT_REACHED),
    ("out_of_credits_message", 403, ErrorKind.LIMIT_REACHED),
    ("out_of_credits_message", 400, ErrorKind.LIMIT_REACHED),
    ("rate_limit", 400, ErrorKind.RATE_LIMITED),  # a rate-limit code is one whatever the status says
    ("unauthorized_text", 401, ErrorKind.AUTH),
    # a specific HTTP status wins over words in the body
    ("out_of_credits_message", 500, ErrorKind.SERVER),
    ("out_of_credits_message", 429, ErrorKind.RATE_LIMITED),
]


def test_every_error_fixture_case_is_asserted_below() -> None:
    assert {case for case, _, _ in ERROR_BODY_EXPECTED} | {"echoes_key"} == set(ERRORS)


@pytest.mark.parametrize(("case", "http_status", "kind"), ERROR_BODY_EXPECTED)
async def test_error_bodies_are_classified_from_status_then_code_then_words(
    h: Harness, case: str, http_status: int, kind: ErrorKind
) -> None:
    body = ERRORS[case]
    if isinstance(body, str):
        h.route.respond(http_status, content=body.encode(), headers={"content-type": "text/plain"})
    else:
        h.route.respond(http_status, json=body)
    result = await h.expect_failure(kind)
    code = body.get("error_details", {}).get("code") if isinstance(body, dict) else None
    if code:
        assert code in (result.error or "")  # the stable code is what an operator searches for


async def test_a_provider_error_text_that_echoes_the_key_is_masked(h: Harness) -> None:
    body = ERRORS["echoes_key"]
    echoing = {
        "error": body["error"].replace("{KEY}", KEY),
        "error_details": {
            **body["error_details"],
            "message": body["error_details"]["message"].replace("{KEY}", KEY),
        },
    }
    h.route.respond(401, json=echoing)
    result = await h.expect_failure(ErrorKind.AUTH)
    assert MASK in (result.error or "")


async def test_provider_error_text_is_bounded(h: Harness) -> None:
    h.route.respond(422, json={"error": "x" * 5000})
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


@pytest.mark.parametrize("body", [[], "just a string", 42, {"people": "nope"}, {"people": [1]}], ids=str)
async def test_search_json_of_the_wrong_shape_is_unknown(search: Harness, body: Any) -> None:
    search.route.respond(200, json=body)
    await search.expect_failure(ErrorKind.UNKNOWN)


async def test_a_redirect_is_reported_not_followed_so_the_key_cannot_travel(h: Harness) -> None:
    await check_redirect_is_not_followed(h)


async def test_the_per_call_timeout_reaches_the_http_client(h: Harness) -> None:
    route = h.route.respond(200, json=OK_BODIES[h.request().capability])
    await h.run(timeout_s=12.5)
    assert route.calls.last.request.extensions["timeout"] == {
        "connect": 12.5,
        "read": 12.5,
        "write": 12.5,
        "pool": 12.5,
    }


# --- connection and capability problems --------------------------------------------------------------------


async def test_missing_env_var_is_an_auth_error_that_names_it_and_makes_no_call(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APOLLO_API_KEY")
    result = await h.expect_failure(ErrorKind.AUTH)
    assert "APOLLO_API_KEY" in (result.error or "")
    assert h.router.calls.call_count == 0


@pytest.mark.parametrize("auth_ref", ["token-store:apollo-01", "PASTED-NOT-A-REF-0123456789"])
async def test_unusable_auth_refs_are_auth_errors_without_echo(h: Harness, auth_ref: str) -> None:
    result = await h.expect_failure(ErrorKind.AUTH, auth_ref=auth_ref)
    assert "PASTED-NOT-A-REF" not in (result.error or "")
    assert h.router.calls.call_count == 0


async def test_an_unsupported_capability_is_a_bad_request_and_makes_no_call(h: Harness) -> None:
    result = await h.expect_failure(ErrorKind.BAD_REQUEST, capability="verify_email")
    assert "verify_email" in (result.error or "")
    assert h.router.calls.call_count == 0


# --- no key leaks, anywhere --------------------------------------------------------------------------------


async def test_failures_and_successes_do_not_print_or_log_the_key(
    h: Harness, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    ok = httpx.Response(200, json=OK_BODIES[h.request().capability])
    await check_no_secret_in_streams(h, capsys, caplog, ok)


async def test_exception_text_containing_the_url_and_key_never_reaches_the_result(h: Harness) -> None:
    h.route.mock(side_effect=httpx.ConnectError(f"cannot connect to {BASE}{SEARCH}?x=1&key={KEY}"))
    result = await h.expect_failure(ErrorKind.SERVER)
    assert "cannot connect" not in (result.error or "")


# --- contract ----------------------------------------------------------------------------------------------


def test_adapter_is_registered_and_is_an_executor() -> None:
    assert ADAPTERS["apollo"] is ApolloAdapter
    adapter = ApolloAdapter(clock=lambda: NOW)
    assert isinstance(adapter, Executor)
    assert set(adapter.capabilities()) == {"find_person", "enrich_company", "find_email"}


async def test_there_is_no_balance_endpoint_and_that_is_reported_not_raised() -> None:
    async with httpx.AsyncClient(verify=False) as client:
        result = await ApolloAdapter(client).balance(
            ConnectionView(id="apollo-01", provider_id="apollo", auth_ref="env:APOLLO_API_KEY")
        )
    assert (result.ok, result.error_kind) == (False, ErrorKind.BAD_REQUEST)


async def test_latency_is_measured(search: Harness) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=SEARCH_CASES["ok"])

    search.route.mock(side_effect=slow)
    result = await search.run()
    assert result.ok and result.latency_ms >= 40


def test_failed_result_asserts_nothing_was_charged() -> None:
    """The helper itself: a failure with units must not pass (otherwise the checks above prove nothing)."""
    bad = ExecResult(ok=False, error_kind=ErrorKind.SERVER, error="x", units_used={"credits": 1.0})
    with pytest.raises(AssertionError):
        assert_failed(bad, ErrorKind.SERVER)
