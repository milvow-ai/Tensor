"""Perplexity executor (Sonar API): answers with sources, usage and cost, failure kinds, and the key staying secret.

No network: ``respx`` answers the one endpoint.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx

from farm import secrets
from farm.capabilities.schemas import AiJobSpec
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.executors.cli_agent import CliAgentExecutor, PerplexityApiExecutor
from farm.executors.cli_agent.perplexity import API_URL

KEY = "pplx-test-0123456789abcdefABCDEF"
KEY_ENV = "PPLX_API_KEY_TEST"


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(KEY_ENV, KEY)
    before = set(secrets._seen)
    yield
    with secrets._lock:
        secrets._seen.intersection_update(before)


def _request(
    *, auth_ref: str = f"env:{KEY_ENV}", meta: dict[str, Any] | None = None, **params: Any
) -> ExecRequest:
    return ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "What is the capital of France?", "mode": "answer", **params},
        connection=ConnectionView(
            id="perplexity-01",
            provider_id="perplexity",
            auth_ref=auth_ref,
            meta={"cli": "perplexity", **(meta or {})},
        ),
        timeout_s=20,
    )


def _everything(result: ExecResult) -> str:
    return json.dumps(result.model_dump(mode="json"), default=str)


ANSWER: dict[str, Any] = {
    "model": "sonar",
    "choices": [{"message": {"role": "assistant", "content": "Paris [1][2]."}}],
    "citations": ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
    "search_results": [
        {"title": "France", "url": "https://example.com/a"},
        {"title": "Paris", "url": "https://example.com/b"},
    ],
    "usage": {
        "prompt_tokens": 12,
        "completion_tokens": 34,
        "total_tokens": 46,
        "cost": {"input_tokens_cost": 0.0001, "output_tokens_cost": 0.0002, "total_cost": 0.0071},
    },
}


@respx.mock
async def test_answer_gets_sources_and_records_usage_and_cost() -> None:
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=ANSWER))
    result = await PerplexityApiExecutor().execute(_request())

    assert result.ok and result.data is not None and result.error_kind is None
    text = result.data["text"]
    assert text.startswith("Paris [1][2].\n\nSources:\n")
    assert "[1] France - https://example.com/a" in text and "[2] Paris - https://example.com/b" in text
    assert "[3] https://example.com/c" in text  # a citation search_results did not list
    assert result.units_used == {"input_tokens": 12.0, "output_tokens": 34.0, "total_tokens": 46.0}
    assert result.cost_usd == Decimal("0.0071") and "cost_estimated" not in result.data
    assert result.data["ai"] == "perplexity" and result.data["model"] == "sonar"
    sent = route.calls.last.request
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(sent.content) == {
        "model": "sonar",
        "messages": [{"role": "user", "content": "What is the capital of France?"}],
    }
    assert KEY not in _everything(result)


@respx.mock
async def test_model_comes_from_the_request_then_the_account() -> None:
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=ANSWER))
    executor = PerplexityApiExecutor()
    await executor.execute(_request(meta={"model": "sonar-pro"}))
    assert json.loads(route.calls.last.request.content)["model"] == "sonar-pro"
    await executor.execute(_request(meta={"model": "sonar-pro"}, model="sonar-reasoning-pro"))
    assert json.loads(route.calls.last.request.content)["model"] == "sonar-reasoning-pro"


@respx.mock
async def test_missing_cost_is_recorded_as_zero_and_marked_estimated() -> None:
    body = {**ANSWER, "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}, "citations": []}
    body.pop("search_results")
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=body))
    result = await PerplexityApiExecutor().execute(_request())
    assert result.ok and result.data is not None
    assert result.cost_usd == Decimal(0) and result.data["cost_estimated"] is True
    assert "Sources:" not in result.data["text"]


@respx.mock
async def test_401_is_auth_and_the_key_an_api_echoes_is_masked() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": f"Invalid API key {KEY}"}})
    )
    result = await PerplexityApiExecutor().execute(_request())
    assert not result.ok and result.error_kind == ErrorKind.AUTH
    assert result.error is not None and "401" in result.error
    assert KEY not in _everything(result)


@respx.mock
async def test_429_is_a_limit_with_the_retry_after_time() -> None:
    respx.post(API_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "17"}, json={"error": {"message": "slow down"}})
    )
    before = datetime.now(UTC)
    result = await PerplexityApiExecutor().execute(_request())
    assert not result.ok and result.error_kind == ErrorKind.RATE_LIMITED
    assert result.retry_after_s == 17.0
    assert result.reset_at is not None
    assert before + timedelta(seconds=16) < result.reset_at < before + timedelta(seconds=19)


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ErrorKind.BAD_REQUEST),
        (404, ErrorKind.BAD_REQUEST),
        (402, ErrorKind.LIMIT_REACHED),
        (403, ErrorKind.AUTH),
        (500, ErrorKind.SERVER),
        (503, ErrorKind.SERVER),
    ],
)
@respx.mock
async def test_status_codes_map_to_failure_kinds(status: int, kind: ErrorKind) -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(status, text="nope"))
    result = await PerplexityApiExecutor().execute(_request())
    assert not result.ok and result.error_kind == kind


@respx.mock
async def test_timeout_and_connection_errors() -> None:
    respx.post(API_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    assert (await PerplexityApiExecutor().execute(_request())).error_kind == ErrorKind.TIMEOUT
    respx.post(API_URL).mock(side_effect=httpx.ConnectError(f"cannot reach host with {KEY}"))
    result = await PerplexityApiExecutor().execute(_request())
    assert result.error_kind == ErrorKind.SERVER and KEY not in _everything(result)


@respx.mock
async def test_an_empty_or_misshapen_answer_is_a_failure() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"choices": [{"message": {"content": " "}}]}))
    assert (await PerplexityApiExecutor().execute(_request())).error_kind == ErrorKind.EMPTY
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"nothing": True}))
    assert (await PerplexityApiExecutor().execute(_request())).error_kind == ErrorKind.SERVER


@respx.mock
async def test_without_a_saved_key_no_request_is_made() -> None:
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=ANSWER))
    executor = PerplexityApiExecutor()
    for ref in ("cli:perplexity-01", "env:PPLX_NOT_SET_ANYWHERE"):
        result = await executor.execute(_request(auth_ref=ref))
        assert not result.ok and result.error_kind == ErrorKind.NEEDS_LOGIN
        assert result.error is not None and "API key" in result.error
    assert not route.called


@respx.mock
async def test_edit_mode_and_bad_model_are_refused_before_any_request() -> None:
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=ANSWER))
    executor = PerplexityApiExecutor()
    assert (await executor.execute(_request(mode="edit"))).error_kind == ErrorKind.BAD_REQUEST
    assert (await executor.execute(_request(model="--bad model"))).error_kind == ErrorKind.BAD_REQUEST
    assert not route.called


@respx.mock
async def test_the_key_never_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=ANSWER))
    with caplog.at_level(logging.DEBUG):
        result = await PerplexityApiExecutor().execute(_request())
    assert result.ok and KEY not in caplog.text


@respx.mock
async def test_the_cli_agent_executor_routes_a_perplexity_account_to_it() -> None:
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=ANSWER))
    result = await CliAgentExecutor().execute(_request())
    assert result.ok and result.data is not None and result.data["ai"] == "perplexity"


def test_ai_start_accepts_perplexity() -> None:
    assert AiJobSpec(task="x", ai="perplexity").ai == "perplexity"
