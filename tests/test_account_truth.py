# ruff: noqa: F811  (the shared fixtures are imported from test_ai_jobs and used by name)
"""What the Farm says about an AI account matches what the account can do (live findings, 2026-10-24).

* D52 a quota failure shows the account limited until its reset, then usable; the old error keeps its kind and
  time and is never read as the state now (agy-02/03/04 showed 9-day-old "Resets in 91h" errors as current)
* D53 Claude's "OAuth session expired and could not be refreshed" is a login failure (claude-01 stayed active)
* D54 a success clears ``last_error`` (claude-02/04 kept success JSON written by the pre-Q0B classifier)
* D55 a model the CLI rejects is a bad request, not a crash, and leaves the account's health alone; the models
  an account lists are checked against the ones its CLI accepts (agy dropped gemini-3.1-pro-high)
* D56 ``list_ais`` reports each account once, in a few hundred characters (about 20k per call before)
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from farm.ai.accounts import ERROR_CHARS, ModelCheck, load_accounts
from farm.executors.base import ConnectionView, ErrorKind
from farm.executors.cli_agent import CliAgentExecutor
from farm.executors.cli_agent.agy import parse_models_list
from farm.executors.cli_agent.classify import classify_agy, classify_claude
from farm.gateway.guide import format_ai_section
from farm.resources import health
from tests.conftest import START
from tests.farm_helpers import fetch
from tests.test_ai_jobs import (  # noqa: F401
    Aip,
    agy_ok,
    aip_factory,
    call,
    claude_ok,
    finish,
    start,
)
from tests.test_q0b_classify import load


def answer(case: tuple[str, str, int]) -> dict[str, Any]:
    """A recorded CLI output as a per-account answer of the fake CLI."""
    stdout, stderr, code = case
    return {"stdout": stdout, "stderr": stderr, "exit_code": code}


def account(report: dict[str, Any], account_id: str) -> dict[str, Any]:
    return next(a for a in report["accounts"] if a["id"] == account_id)


def pool_of(report: dict[str, Any], ai: str) -> dict[str, Any]:
    return next(p for p in report["ais"] if p["id"] == ai)


def at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


# --- D53 / D55: the classifier tables ---------------------------------------------------------------------------


def test_claude_oauth_session_expired_needs_login() -> None:
    verdict = classify_claude(*load("claude", "oauth_expired"))
    assert (verdict.ok, verdict.kind) == (False, ErrorKind.NEEDS_LOGIN)
    assert verdict.message == "Failed to authenticate: OAuth session expired and could not be refreshed"


def test_claude_refresh_held_by_another_process_is_not_a_login_failure() -> None:
    # Claude Code's own text for a refresh that another of its processes holds: transient, retry in a minute.
    body = json.dumps(
        {
            "is_error": True,
            "api_error_status": None,
            "result": "Failed to refresh OAuth token: another Claude Code process is refreshing it or exited "
            "mid-refresh. This is usually transient; retry in a minute",
        }
    )
    assert classify_claude(body, "", 1).kind == ErrorKind.UNKNOWN


def test_agy_rejected_model_is_a_bad_request_stated_once() -> None:
    verdict = classify_agy(*load("agy", "model_rejected"))
    assert (verdict.ok, verdict.kind) == (False, ErrorKind.BAD_REQUEST)
    assert verdict.message is not None
    assert verdict.message.count("is not recognized as a known model") == 1  # JSON error + stderr: kept once


def test_claude_unknown_model_is_a_bad_request() -> None:
    body = json.dumps(
        {
            "is_error": True,
            "api_error_status": 404,
            "result": "There's an issue with the selected model (claude-nope). It may not exist or you may not "
            "have access to it. Run /model to pick a different model.",
        }
    )
    assert classify_claude(body, "", 1).kind == ErrorKind.BAD_REQUEST


_NEW_PHRASES = st.sampled_from(
    [
        "Failed to authenticate: OAuth session expired and could not be refreshed",
        "invalid model selection",
        "model x is not recognized as a known model",
        "There's an issue with the selected model",
    ]
)


@given(st.lists(_NEW_PHRASES, min_size=1, max_size=4).map(" ".join))
def test_an_answer_quoting_the_new_phrases_is_still_a_success(text: str) -> None:
    assert classify_claude(json.dumps({"is_error": False, "result": text}), "", 0).ok
    assert classify_agy(json.dumps({"status": "SUCCESS", "response": text}), "", 0).ok


# --- D55: configured models against the ones the CLI lists ------------------------------------------------------

LIVE_AGY_MODELS = [
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
]


def test_agy_models_output_gives_the_model_ids() -> None:
    stdout, stderr, _ = load("agy", "models_list")
    assert parse_models_list(stdout) == LIVE_AGY_MODELS
    assert parse_models_list(stderr) == []  # "Fetching available models..."


def test_model_check_names_each_model_the_cli_does_not_accept() -> None:
    accepted = tuple(LIVE_AGY_MODELS)
    stale = ModelCheck("agy-01", ("gemini-3.8-flash-high", "gemini-3.1-pro-high"), accepted)
    assert stale.rejected == ["gemini-3.1-pro-high"]
    assert "gemini-3.1-pro-high" in stale.describe() and "farm registry sync" in stale.describe()
    assert ModelCheck("agy-01", ("GEMINI-3.8-FLASH-HIGH",), accepted).rejected == []  # as `offers` compares
    assert "none configured" in ModelCheck("agy-02", (), accepted).describe()
    unlisted = ModelCheck("claude-02", ("sonnet",), None)  # the CLI cannot list: nothing is claimed
    assert unlisted.rejected == [] and "not checked" in unlisted.describe()


async def test_list_models_runs_agy_models_with_the_account_launch(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(claude=1, gemini=1)
    aip.harness.set_response(**answer(load("agy", "models_list")))
    (agy,) = await load_accounts(aip.pool, ids=["agy-01"])
    view = ConnectionView(id="agy-01", provider_id="gemini", auth_ref="cli:agy-01", meta=dict(agy.meta))

    accepted = await CliAgentExecutor().list_models(view)

    assert accepted == LIVE_AGY_MODELS
    (event,) = aip.harness.events()
    assert event["argv"] == ["models"] and event["userprofile"] == agy.meta["home"]  # the account's own home
    assert agy.models_check(accepted).rejected == []
    aip.harness.set_response(stderr="error: not signed in", exit_code=1)
    assert await CliAgentExecutor().list_models(view) is None  # could not list: no verdict


async def test_a_model_the_cli_rejects_fails_the_job_as_bad_request_and_spares_the_account(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(claude=1, gemini=1)
    aip.harness.set_response(**answer(load("agy", "model_rejected")))
    async with aip.client() as client:
        job = await start(client, ai="gemini", account="agy-01", model="gemini-3.8-flash-high")
        (result,) = await finish(client, [job["job_id"]])
        listing = await call(client, "list_ais")
    assert result["ok"] is False and result["error"]["kind"] == "bad_request"  # was "crash"
    assert "not recognized as a known model" in result["error"]["message"]
    agy = account(listing, "agy-01")
    assert agy["usable"] is True and "last_error" not in agy
    rows = await fetch(
        aip.pool, "select failure_count, consecutive_failures from connection_health where connection_id = %s",
        "agy-01",
    )
    assert rows in ([], [(0, 0)])  # the account's health is neither helped nor hurt


# --- D52: a quota failure, until its reset and after ------------------------------------------------------------


async def test_a_quota_failure_shows_the_account_limited_until_the_reset_then_usable(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(claude=1, gemini=2)
    limit = load("agy", "limit")  # "Resets in 91h35m7s"
    aip.harness.set_response(stdout=agy_ok("from agy-02"), per_account={"agy-01": answer(limit)})
    reset = datetime.now(UTC) + timedelta(hours=91, minutes=35, seconds=7)  # the CLI's relative reset

    async with aip.client() as client:
        reply = await call(client, "ask_ai", ai="gemini", task="research")
        during = await call(client, "list_ais")
    guide_during = await format_ai_section(aip.pool, aip.clock())

    assert reply["ok"] is True and reply["result"]["connection_id"] == "agy-02"  # failed over
    limited = account(during, "agy-01")
    assert (limited["status"], limited["usable"], limited["unusable"]) == ("exhausted", False, "exhausted")
    assert abs(at(limited["back_at"]) - reset) < timedelta(minutes=1)
    assert limited["last_error"]["kind"] == "limit_reached" and at(limited["last_error"]["at"]) == START
    assert (pool_of(during, "gemini")["usable"], pool_of(during, "gemini")["total"]) == (1, 2)
    assert "### Gemini (`gemini`)\n- Accounts usable now: 1/2" in guide_during
    assert reset.strftime("%Y-%m-%d") in guide_during  # a reset days away is shown with its date

    aip.clock.now = reset + timedelta(minutes=5)
    await health.reactivate_due(aip.pool, aip.clock())
    async with aip.client() as client:
        after = await call(client, "list_ais")
    back = account(after, "agy-01")
    assert (back["status"], back["usable"]) == ("active", True)
    assert {"unusable", "back_at", "reset_at"}.isdisjoint(back)
    # still the last failure, dated, so it is never read as the account's state now
    assert back["last_error"]["kind"] == "limit_reached" and at(back["last_error"]["at"]) == START
    assert "Accounts usable now: 2/2" in await format_ai_section(aip.pool, aip.clock())


# --- D53: an expired Claude login ------------------------------------------------------------------------------


async def test_an_expired_claude_login_marks_the_account_needs_login(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(claude=2, gemini=1)
    aip.harness.set_response(
        stdout=claude_ok("from claude-03"), per_account={"claude-02": answer(load("claude", "oauth_expired"))}
    )
    async with aip.client() as client:
        reply = await call(client, "ask_ai", ai="claude", task="hello")
        listing = await call(client, "list_ais")

    assert reply["ok"] is True and reply["result"]["connection_id"] == "claude-03"
    expired = account(listing, "claude-02")
    assert (expired["status"], expired["usable"], expired["unusable"]) == ("needs_login", False, "needs_login")
    assert expired["login_state"] == "needs_login" and "back_at" not in expired  # only a login brings it back
    assert expired["last_error"]["kind"] == "needs_login"
    assert "### Claude (`claude`)\n- Accounts usable now: 1/2" in await format_ai_section(aip.pool, aip.clock())


# --- D54: last_error holds failures only ------------------------------------------------------------------------


async def test_a_success_clears_the_last_error(aip_factory: Callable[..., Awaitable[Aip]]) -> None:
    aip = await aip_factory(claude=2, gemini=1)
    aip.harness.set_response(
        stdout=claude_ok("fine"), per_account={"claude-02": {"stderr": "segfault", "exit_code": 139}}
    )
    async with aip.client() as client:
        first = await call(client, "ask_ai", ai="claude", task="one")  # claude-02 crashes, claude-03 answers
        failed = account(await call(client, "list_ais"), "claude-02")
        aip.harness.set_response(stdout=claude_ok("fine"))
        job = await start(client, ai="claude", account="claude-02", task="two")
        (second,) = await finish(client, [job["job_id"]])
        recovered = account(await call(client, "list_ais"), "claude-02")

    assert first["result"]["connection_id"] == "claude-03" and second["ok"] is True
    assert failed["last_error"]["kind"] == "unknown" and failed["last_error"]["message"] == "segfault"
    assert "last_error" not in recovered
    rows = await fetch(
        aip.pool,
        "select last_error, last_error_kind, last_error_at from connection_health where connection_id = %s",
        "claude-02",
    )
    assert rows == [(None, None, None)]


async def test_a_success_inside_a_newer_cooldown_keeps_the_failure_that_explains_it(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(claude=1, gemini=1)
    await health.record_failure(
        aip.pool, "claude-02", ErrorKind.RATE_LIMITED, "slow down", now=START, retry_after_s=60
    )
    sql = "select last_error, last_error_kind from connection_health where connection_id = %s"

    # a call that started before the rate limit ends after it: the cooldown and its reason stay
    await health.record_success(aip.pool, "claude-02", now=START + timedelta(seconds=1))
    assert await fetch(aip.pool, sql, "claude-02") == [("slow down", "rate_limited")]

    await health.record_success(aip.pool, "claude-02", now=START + timedelta(seconds=61))
    assert await fetch(aip.pool, sql, "claude-02") == [(None, None)]


# --- D56: list_ais is compact ------------------------------------------------------------------------------------


async def test_list_ais_reports_each_account_once_in_a_few_hundred_characters(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    aip = await aip_factory(claude=3, gemini=2, codex=1, hermes=1)
    sign_in = (
        "Eligibility check failed: Your current account is not eligible for Antigravity. Verify your account to "
        "continue.\n\nPlease verify your account in your browser to continue:\n"
        "https://accounts.google.com/signin/continue?sarp=1&scc=1&plt=" + "A" * 300
    )
    await health.record_failure(aip.pool, "agy-02", ErrorKind.NEEDS_LOGIN, sign_in, now=START)
    async with aip.client() as client:
        report = await call(client, "list_ais")

    ids = [a["id"] for a in report["accounts"]]
    assert sorted(ids) == ["agy-01", "agy-02", "claude-02", "claude-03", "claude-04", "codex-01", "hermes-01"]
    assert all("accounts" not in ai for ai in report["ais"])  # no second copy of the accounts
    message = account(report, "agy-02")["last_error"]["message"]
    assert len(message) <= ERROR_CHARS and "https://" not in message and "\n" not in message
    assert "<link>" in message or message.endswith("…")
    sizes = [len(json.dumps(a)) for a in report["accounts"]]  # the one with an error included
    assert max(sizes) <= 560 and len(json.dumps(report)) <= 300 * len(ids) + 220 * len(report["ais"]), sizes
