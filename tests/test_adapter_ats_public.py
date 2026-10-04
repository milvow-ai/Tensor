"""Public ATS boards (Greenhouse, Lever, Ashby), fully offline (respx). No key, no quota.

The behaviour that matters most: an outage must never read as "this company has no job board".
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from farm.adapters import ADAPTERS, AtsPublicAdapter
from farm.adapters.ats_public import slug_candidates
from farm.capabilities.schemas import JobsLookupOut
from farm.executors.base import ErrorKind, ExecResult, Executor
from tests.adapter_testkit import (
    MALFORMED_BODIES,
    MALFORMED_IDS,
    NOW,
    TIMEOUT_EXCS,
    TRANSPORT_EXCS,
    assert_empty,
    assert_failed,
    exc_id,
    load_cases,
    make_request,
)

GREENHOUSE = "https://boards-api.greenhouse.io"
LEVER = "https://api.lever.co"
ASHBY = "https://api.ashbyhq.com"

GH = load_cases("ats_public", "greenhouse_jobs.json")
LV = load_cases("ats_public", "lever_postings.json")
AS = load_cases("ats_public", "ashby_board.json")


def gh_url(token: str) -> str:
    return f"{GREENHOUSE}/v1/boards/{token}/jobs"


def lever_url(token: str) -> str:
    return f"{LEVER}/v0/postings/{token}"


def ashby_url(token: str) -> str:
    return f"{ASHBY}/posting-api/job-board/{token}"


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    # No base_url: three hosts are in play. Any call that is not mocked fails the test (assert_all_mocked).
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def adapter() -> AsyncIterator[AtsPublicAdapter]:
    async with httpx.AsyncClient(verify=False) as client:
        yield AtsPublicAdapter(client, clock=lambda: NOW)


def request(**params: Any) -> Any:
    return make_request(
        provider="ats_public",
        connection_id="ats-public-01",
        auth_ref="cli:none",  # the registry's placeholder; it must never be resolved
        capability="jobs_lookup",
        params=params,
    )


async def run(adapter: AtsPublicAdapter, **params: Any) -> ExecResult:
    return await adapter.execute(request(**params))


def out_of(result: ExecResult) -> JobsLookupOut:
    return JobsLookupOut.model_validate(result.data)


# --- one platform at a time --------------------------------------------------------------------------------


async def test_greenhouse_board(api: respx.MockRouter, adapter: AtsPublicAdapter) -> None:
    route = api.get(gh_url("vaulttec")).respond(200, json=GH["ok"])
    result = await run(adapter, board="vaulttec", ats="greenhouse")

    assert result.ok is True and result.found is True and result.error_kind is None and result.error is None
    assert result.units_used == {"requests": 1.0} and result.cost_usd == 0
    out = out_of(result)
    assert (out.source.provider, out.source.connection_id, out.observed_at) == (
        "ats_public",
        "ats-public-01",
        NOW,
    )
    assert (out.platform, out.board, out.board_guessed, out.total) == ("greenhouse", "vaulttec", False, 2)
    first, second = out.postings
    assert (first.id, first.title, first.location) == ("127817", "Vault Designer", "NYC")
    assert first.url == "https://boards.greenhouse.io/vaulttec/jobs/127817" and first.platform == "greenhouse"
    assert second.location == "Remote - US"
    request_ = route.calls.last.request
    assert request_.method == "GET" and dict(request_.url.params) == {}
    assert "authorization" not in request_.headers and "x-api-key" not in request_.headers


async def test_lever_postings(api: respx.MockRouter, adapter: AtsPublicAdapter) -> None:
    route = api.get(lever_url("acme")).respond(200, json=LV["ok"])
    result = await run(adapter, board="acme", ats="lever")
    out = out_of(result)
    assert (out.platform, out.total, len(out.postings)) == (
        "lever",
        3,
        3,
    )  # the posting without a URL is dropped
    backend, warehouse, sales = out.postings
    assert (backend.id, backend.title, backend.location) == (
        "5ac21346-8e0c-4494-8e7a-3eb92ff77902",
        "Backend Engineer",
        "San Francisco",
    )
    assert (backend.department, backend.employment_type, backend.remote) == ("Engineering", "Full-time", True)
    assert (backend.salary_min, backend.salary_max) == (150000.0, 190000.0)
    assert backend.posted_at == datetime.fromtimestamp(1759579200, tz=UTC)
    assert backend.url == "https://jobs.lever.co/acme/5ac21346-8e0c-4494-8e7a-3eb92ff77902"
    assert (warehouse.remote, warehouse.salary_min, warehouse.salary_max) == (
        False,
        None,
        None,
    )  # hourly: not annual
    assert (warehouse.department, warehouse.employment_type) == ("Operations", "Part-time")  # team stands in
    assert (sales.remote, sales.posted_at, sales.employment_type) == (None, None, None)
    assert dict(route.calls.last.request.url.params) == {"mode": "json"}


async def test_ashby_board(api: respx.MockRouter, adapter: AtsPublicAdapter) -> None:
    route = api.get(ashby_url("example")).respond(200, json=AS["ok"])
    result = await run(adapter, board="example", ats="ashby")
    out = out_of(result)
    assert (out.platform, out.total) == (
        "ashby",
        2,
    )  # the unlisted role and the one without a URL are left out
    pm, support = out.postings
    assert (
        pm.id == "4d2f1c9e-1111-4aaa-8bbb-000000000001"
    )  # the documented fields have no id: last jobUrl segment
    assert (pm.title, pm.location, pm.department, pm.employment_type, pm.remote) == (
        "Product Manager",
        "Houston, TX",
        "Product",
        "FullTime",
        True,
    )
    assert (pm.salary_min, pm.salary_max) == (81000.0, 87000.0)
    assert pm.posted_at == datetime(2021, 4, 30, 16, 21, 55, 393000, tzinfo=UTC)
    assert (support.remote, support.salary_min, support.salary_max) == (
        False,
        None,
        None,
    )  # hourly: not annual
    assert dict(route.calls.last.request.url.params) == {"includeCompensation": "true"}


@pytest.mark.parametrize("ats", ["greenhouse", "lever", "ashby"])
async def test_a_board_that_is_open_but_has_no_postings_is_an_empty_result(
    api: respx.MockRouter, adapter: AtsPublicAdapter, ats: str
) -> None:
    empty = {"greenhouse": GH["empty"], "lever": LV["empty"], "ashby": AS["empty"]}[ats]
    url = {"greenhouse": gh_url, "lever": lever_url, "ashby": ashby_url}[ats]("quiet")
    api.get(url).respond(200, json=empty)
    result = await run(adapter, board="quiet", ats=ats)
    assert_empty(result, units={"requests": 1.0})
    out = out_of(result)
    assert (out.platform, out.board, out.postings) == (ats, "quiet", [])
    assert "no open postings" in (result.error or "")


async def test_the_limit_truncates_but_the_total_is_kept(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    api.get(gh_url("vaulttec")).respond(200, json=GH["ok"])
    out = out_of(await run(adapter, board="vaulttec", ats="greenhouse", limit=1))
    assert (len(out.postings), out.total) == (1, 2)


# --- finding the platform ----------------------------------------------------------------------------------


async def test_platforms_are_tried_in_order_and_a_404_just_moves_on(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    greenhouse = api.get(gh_url("acme")).respond(404, json={"status": 404, "error": "Job not found"})
    lever = api.get(lever_url("acme")).respond(200, json=LV["ok"])
    ashby = api.get(ashby_url("acme")).respond(200, json=AS["ok"])
    result = await run(adapter, board="acme")
    assert out_of(result).platform == "lever"
    assert result.units_used == {"requests": 2.0}  # the real number of HTTP calls
    assert (greenhouse.call_count, lever.call_count, ashby.call_count) == (1, 1, 0)  # first answer wins


async def test_the_first_platform_that_answers_wins_even_with_an_empty_list(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    api.get(gh_url("acme")).respond(200, json=GH["empty"])
    lever = api.get(lever_url("acme")).respond(200, json=LV["ok"])
    result = await run(adapter, board="acme")
    assert_empty(result, units={"requests": 1.0})
    assert not lever.called  # a 200 means the company runs its hiring there


async def test_no_board_anywhere_is_an_empty_result_that_counts_every_probe(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    for url in (gh_url("nobody"), lever_url("nobody"), ashby_url("nobody")):
        api.get(url).respond(404, json={"error": "not found"})
    result = await run(adapter, board="nobody")
    assert_empty(result, units={"requests": 3.0})
    assert "nobody" in (result.error or "")
    assert out_of(result).postings == []


async def test_a_company_name_derives_slugs_and_says_the_board_was_guessed(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    for token in ("bigdata",):
        for url in (gh_url(token), lever_url(token), ashby_url(token)):
            api.get(url).respond(404, json={})
    hit = api.get(gh_url("big-data")).respond(200, json=GH["ok"])
    result = await run(adapter, company="Big Data Co.")
    out = out_of(result)
    assert (out.board, out.board_guessed, out.platform) == ("big-data", True, "greenhouse")
    assert hit.called
    assert result.units_used == {"requests": 4.0}


async def test_an_explicit_board_is_never_marked_guessed(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    api.get(gh_url("acme")).respond(200, json=GH["ok"])
    assert out_of(await run(adapter, board="acme", company="Some Other Name")).board_guessed is False


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ("Acme", ["acme"]),
        ("Acme Corp.", ["acme"]),
        ("ACME, Inc.", ["acme"]),
        ("Big Data Co", ["bigdata", "big-data"]),
        ("Müller & Söhne GmbH", ["mullersohne", "muller-sohne"]),
        ("Co", ["co"]),  # a lone suffix word is the name
        ("3M", ["3m"]),
        ("!!!", []),
        ("   ", []),
    ],
)
def test_slug_candidates(company: str, expected: list[str]) -> None:
    assert slug_candidates(company) == expected


# --- an outage is not "no board" ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.SERVER),
        (503, ErrorKind.SERVER),
        (403, ErrorKind.AUTH),
        (400, ErrorKind.BAD_REQUEST),
        (408, ErrorKind.TIMEOUT),
    ],
)
async def test_a_failing_platform_is_raised_when_no_other_platform_answers(
    api: respx.MockRouter, adapter: AtsPublicAdapter, status: int, kind: ErrorKind
) -> None:
    api.get(gh_url("acme")).respond(status, headers={"Retry-After": "9"})
    api.get(lever_url("acme")).respond(404, json={})
    api.get(ashby_url("acme")).respond(404, json={})
    result = await run(adapter, board="acme")
    assert_failed(result, kind)  # NOT an empty result: the board may well exist
    assert result.retry_after_s == 9.0  # the header is honoured whatever the status


async def test_a_failing_platform_is_ignored_when_another_one_answers(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    api.get(gh_url("acme")).respond(500)
    api.get(lever_url("acme")).respond(200, json=LV["ok"])
    result = await run(adapter, board="acme")
    assert result.ok is True and out_of(result).platform == "lever"


@pytest.mark.parametrize("exc", TIMEOUT_EXCS, ids=exc_id)
async def test_timeouts_are_classified_not_raised(
    api: respx.MockRouter, adapter: AtsPublicAdapter, exc: httpx.TimeoutException
) -> None:
    api.get(gh_url("acme")).mock(side_effect=exc)
    result = await adapter.execute(request(board="acme", ats="greenhouse"))
    assert_failed(result, ErrorKind.TIMEOUT)


@pytest.mark.parametrize("exc", TRANSPORT_EXCS, ids=exc_id)
async def test_transport_failures_are_classified_as_server_errors(
    api: respx.MockRouter, adapter: AtsPublicAdapter, exc: httpx.RequestError
) -> None:
    api.get(gh_url("acme")).mock(side_effect=exc)
    result = await adapter.execute(request(board="acme", ats="greenhouse"))
    assert_failed(result, ErrorKind.SERVER)
    assert type(exc).__name__ in (result.error or "")


@pytest.mark.parametrize("content", MALFORMED_BODIES, ids=MALFORMED_IDS)
async def test_malformed_json_is_unknown_not_an_empty_board(
    api: respx.MockRouter, adapter: AtsPublicAdapter, content: bytes
) -> None:
    api.get(gh_url("acme")).respond(200, content=content)
    api.get(lever_url("acme")).respond(404, json={})
    api.get(ashby_url("acme")).respond(404, json={})
    assert_failed(await run(adapter, board="acme"), ErrorKind.UNKNOWN)


@pytest.mark.parametrize(
    ("ats", "body"),
    [
        ("greenhouse", GH["no_jobs_key"]),
        ("greenhouse", []),
        ("lever", LV["wrong_shape"]),
        ("lever", "text"),
        ("ashby", AS["wrong_shape"]),
        ("ashby", []),
    ],
    ids=str,
)
async def test_json_of_the_wrong_shape_is_unknown(
    api: respx.MockRouter, adapter: AtsPublicAdapter, ats: str, body: Any
) -> None:
    url = {"greenhouse": gh_url, "lever": lever_url, "ashby": ashby_url}[ats]("acme")
    api.get(url).respond(200, json=body)
    assert_failed(await run(adapter, board="acme", ats=ats), ErrorKind.UNKNOWN)


async def test_unusable_entries_are_skipped_not_fatal(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    api.get(gh_url("acme")).respond(200, json=GH["unusable"])
    assert_empty(await run(adapter, board="acme", ats="greenhouse"), units={"requests": 1.0})


async def test_a_redirect_is_reported_not_followed(api: respx.MockRouter, adapter: AtsPublicAdapter) -> None:
    api.get(gh_url("acme")).respond(302, headers={"location": "https://elsewhere.example/jobs"})
    assert_failed(await run(adapter, board="acme", ats="greenhouse"), ErrorKind.UNKNOWN)


async def test_the_callers_timeout_is_one_budget_shared_by_all_probes(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    route = api.get(gh_url("acme")).respond(404, json={})
    result = await adapter.execute(
        make_request(
            provider="ats_public",
            connection_id="c",
            auth_ref="cli:none",
            capability="jobs_lookup",
            params={"board": "acme"},
            timeout_s=0.0,
        )
    )
    assert_failed(result, ErrorKind.TIMEOUT)
    assert not route.called


async def test_each_probe_gets_only_the_time_that_is_left(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    route = api.get(gh_url("acme")).respond(404, json={})
    api.get(lever_url("acme")).respond(404, json={})
    api.get(ashby_url("acme")).respond(404, json={})
    await adapter.execute(
        make_request(
            provider="ats_public",
            connection_id="c",
            auth_ref="cli:none",
            capability="jobs_lookup",
            params={"board": "acme"},
            timeout_s=20.0,
        )
    )
    assert 0 < route.calls.last.request.extensions["timeout"]["read"] <= 20.0


# --- input -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"what": "engineer"},
        {"where": "london"},
        {"board": "a/b"},
        {"board": ".."},
        {"board": "x" * 101},
        {"board": "acme", "ats": "workday"},
        {"ats": "lever"},
        {"board": "acme", "limit": 0},
        {"board": "acme", "surprise": 1},
    ],
)
async def test_invalid_or_unsupported_input_is_a_bad_request_and_makes_no_call(
    api: respx.MockRouter, adapter: AtsPublicAdapter, params: dict[str, Any]
) -> None:
    assert_failed(await run(adapter, **params), ErrorKind.BAD_REQUEST)
    assert api.calls.call_count == 0


async def test_a_company_name_with_no_letters_is_a_bad_request(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    result = await run(adapter, company="!!!")
    assert_failed(result, ErrorKind.BAD_REQUEST)
    assert "usable" in (result.error or "")
    assert api.calls.call_count == 0


async def test_search_requests_say_that_adzuna_is_the_right_pool(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    result = await run(adapter, what="engineer", where="london")
    assert_failed(result, ErrorKind.BAD_REQUEST)
    assert "Adzuna" in (result.error or "")


async def test_a_board_token_is_quoted_into_the_path(
    api: respx.MockRouter, adapter: AtsPublicAdapter
) -> None:
    route = api.get(gh_url("a.b_c-d")).respond(200, json=GH["ok"])
    await run(adapter, board="a.b_c-d", ats="greenhouse")
    assert route.called


# --- no credential is involved -----------------------------------------------------------------------------


@pytest.mark.parametrize("auth_ref", ["cli:none", "env:SURELY_NOT_SET_ANYWHERE", "not even a reference"])
async def test_the_placeholder_auth_ref_is_never_resolved(
    api: respx.MockRouter, adapter: AtsPublicAdapter, auth_ref: str
) -> None:
    """Resolving any of these would fail (cli: unimplemented, variable unset, junk text)."""
    api.get(gh_url("acme")).respond(200, json=GH["ok"])
    result = await adapter.execute(
        make_request(
            provider="ats_public",
            connection_id="c",
            auth_ref=auth_ref,
            capability="jobs_lookup",
            params={"board": "acme"},
        )
    )
    assert result.ok is True


async def test_no_authorisation_header_is_ever_sent(api: respx.MockRouter, adapter: AtsPublicAdapter) -> None:
    route = api.get(lever_url("acme")).respond(200, json=LV["ok"])
    await run(adapter, board="acme", ats="lever")
    sent = {name.lower() for name in route.calls.last.request.headers}
    assert not sent & {"authorization", "x-api-key", "x-goog-api-key", "cookie"}


def test_adapter_is_registered_and_is_an_executor() -> None:
    assert ADAPTERS["ats_public"] is AtsPublicAdapter
    adapter = AtsPublicAdapter(clock=lambda: NOW)
    assert isinstance(adapter, Executor)
    assert set(adapter.capabilities()) == {"jobs_lookup"}
    assert AtsPublicAdapter.requires_auth is False
