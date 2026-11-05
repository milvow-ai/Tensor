"""Q0B: CLI outcome classification built from real outputs (tests/fixtures/cli/, docs/QUALITY.md D1/D4)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from farm.executors.base import ErrorKind
from farm.executors.cli_agent.classify import (
    Classification,
    classify_agy,
    classify_claude,
    classify_codex,
    classify_gemini,
    classify_hermes,
    parse_reset,
)

FIX = Path(__file__).parent / "fixtures" / "cli"

TRIGGER_WORDS = (
    "The rate limit is 429, capacity is low, it resets in 5 hours, please log in with /login, "
    "error 401 and 403 unauthorized, quota exceeded, overloaded."
)


def load(cli: str, case: str) -> tuple[str, str, int]:
    base = FIX / cli / case
    out = base.with_suffix(".stdout").read_text(encoding="utf-8")
    err = base.with_suffix(".stderr").read_text(encoding="utf-8")
    code = int(base.with_suffix(".exit").read_text(encoding="utf-8").strip())
    return out, err, code


@pytest.mark.parametrize("case", ["success", "success_trigger_words"])
def test_claude_success_fixtures(case: str) -> None:
    assert classify_claude(*load("claude", case)).ok


@pytest.mark.parametrize("case", ["success", "success_trigger_words"])
def test_agy_success_fixtures(case: str) -> None:
    assert classify_agy(*load("agy", case)).ok


def test_claude_not_logged_in_fixture() -> None:
    v = classify_claude(*load("claude", "not_logged_in"))
    assert not v.ok
    assert v.kind == ErrorKind.NEEDS_LOGIN


def test_agy_limit_fixture_with_reset() -> None:
    before = datetime.now(UTC)
    v = classify_agy(*load("agy", "limit"))
    assert not v.ok
    assert v.kind == ErrorKind.LIMIT_REACHED
    assert v.reset_at is not None
    expected = timedelta(hours=91, minutes=35, seconds=7)
    assert expected - timedelta(seconds=5) <= v.reset_at - before <= expected + timedelta(seconds=5)


def test_agy_not_eligible_fixture() -> None:
    v = classify_agy(*load("agy", "not_eligible"))
    assert not v.ok
    assert v.kind == ErrorKind.NEEDS_LOGIN


def test_hermes_auth_fixture() -> None:
    out, err, code = load("hermes", "auth")
    usage = json.loads((FIX / "hermes" / "auth.usage.json").read_text(encoding="utf-8"))
    v = classify_hermes(out, err, code, usage)
    assert not v.ok
    assert v.kind == ErrorKind.NEEDS_LOGIN


def test_codex_rollout_error_is_not_needs_login() -> None:
    v = classify_codex(*load("codex", "rollout_error"))
    assert not v.ok
    assert v.kind == ErrorKind.UNKNOWN
    assert v.kind != ErrorKind.NEEDS_LOGIN


def test_codex_rollout_noise_on_success_is_ignored() -> None:
    stdout = '{"type":"item.completed","item":{"text":"hi"}}\n{"type":"turn.completed","usage":{}}\n'
    stderr = (FIX / "codex" / "rollout_error.stderr").read_text(encoding="utf-8")
    assert classify_codex(stdout, stderr, 0).ok


def test_exit_zero_stderr_noise_is_not_an_error() -> None:
    assert classify_agy('{"status":"SUCCESS","response":"x"}', "warning: rate limit /login 401", 0).ok
    assert classify_claude('{"is_error":false,"result":"x"}', "429 overloaded", 0).ok


def test_unrecognised_failure_is_unknown() -> None:
    assert classify_claude("", "segfault", 139).kind == ErrorKind.UNKNOWN
    assert classify_gemini("", "boom", 2).kind == ErrorKind.UNKNOWN


def test_gemini_structured_error() -> None:
    body = json.dumps({"error": {"code": 429, "message": "Quota exceeded for model"}})
    v = classify_gemini(body, "", 1)
    assert v.kind == ErrorKind.LIMIT_REACHED
    assert classify_gemini('{"response":"ok","stats":{}}', "", 0).ok


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("Resets in 91h35m7s.", 91 * 3600 + 35 * 60 + 7),
        ("resets in 45m", 45 * 60),
        ("Resets in 2d3h", 2 * 86400 + 3 * 3600),
        ("resets in 5 hours", 5 * 3600),
    ],
)
def test_parse_reset_relative(text: str, seconds: int) -> None:
    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    got = parse_reset(text, now=now)
    assert got is not None
    # the legacy "N hours" parser uses the real clock; allow a few seconds of skew
    ref = now if "hours" not in text else datetime.now(UTC)
    assert abs((got - ref).total_seconds() - seconds) < 5


def test_parse_reset_iso_and_none() -> None:
    assert parse_reset("Resets at 2026-10-04T18:00:00Z") == datetime(2026, 10, 4, 18, tzinfo=UTC)
    assert parse_reset("nothing here") is None


# --- property: a structured success is a success whatever the answer says ------------------------------------------

_WORDS = st.sampled_from(
    [
        "rate limit", "429", "capacity", "resets in 5 hours", "Resets in 91h35m7s", "please log in", "/login",
        "401", "403", "unauthorized", "quota exceeded", "overloaded", "usage limit", "not logged in",
        "RESOURCE_EXHAUSTED", "Individual quota reached", "insufficient_quota", "invalid api key", "token expired",
        "authentication required", "HTTP 401", "not eligible for Antigravity", "rejected your API key",
    ]
)
_ANSWERS = st.one_of(st.lists(_WORDS, min_size=1, max_size=8).map(" ".join), st.text(max_size=80))


def _success_cases(answer: str) -> list[Classification]:
    noise = "rate limit 429 /login unauthorized"  # stderr noise of a run that succeeded
    return [
        classify_claude(json.dumps({"is_error": False, "result": answer, "session_id": "s"}), noise, 0),
        classify_agy(json.dumps({"status": "SUCCESS", "response": answer}), noise, 0),
        classify_gemini(json.dumps({"response": answer, "stats": {}}), noise, 0),
        classify_codex(
            json.dumps({"type": "item.completed", "item": {"text": answer}})
            + '\n{"type":"turn.completed","usage":{}}\n',
            noise,
            0,
        ),
        classify_hermes(answer, noise, 0, {"failed": False, "session_id": "s"}),
    ]


@given(_ANSWERS)
def test_structured_success_is_always_success(answer: str) -> None:
    for verdict in _success_cases(answer):
        assert verdict.ok, verdict


def test_trigger_word_answer_is_success_for_every_cli() -> None:
    assert all(v.ok for v in _success_cases(TRIGGER_WORDS))


def test_fixture_dirs_are_documented() -> None:
    readme = (FIX / "README.md").read_text(encoding="utf-8")
    for p in FIX.glob("*/*.stdout"):
        assert f"{p.parent.name}/{p.stem}" in readme, p


def test_classification_is_frozen() -> None:
    v: Any = Classification(ok=True)
    with pytest.raises(AttributeError):
        v.ok = False

# --- R1 regression tests: false account exhaustion, terminal events, stdout prose isolation --------


def test_r1_server_overload_and_capacity() -> None:
    # 1. Overload -> SERVER
    v1 = classify_claude(
        json.dumps({"is_error": True, "error": "Server is temporarily overloaded."}), "", 1
    )
    assert not v1.ok
    assert v1.kind == ErrorKind.SERVER

    v2 = classify_codex("", "Error: OpenAI engine overloaded. Please try again later.", 1)
    assert not v2.ok
    assert v2.kind == ErrorKind.SERVER

    # 2. At capacity -> SERVER
    v3 = classify_claude(
        json.dumps({"is_error": True, "error": "Anthropic is currently at capacity."}), "", 1
    )
    assert not v3.ok
    assert v3.kind == ErrorKind.SERVER

    # 3. Model capacity -> SERVER
    v4 = classify_agy("", "AGY_ERROR: {\"short_error\": \"model capacity exceeded\", \"error_code\": 503}", 1)
    assert not v4.ok
    assert v4.kind == ErrorKind.SERVER

    v5 = classify_gemini(
        json.dumps({"error": {"code": 503, "message": "The model-capacity limit was hit."}}), "", 1
    )
    assert not v5.ok
    assert v5.kind == ErrorKind.SERVER

    # 4. HTTP 529 / 503 -> SERVER
    v6 = classify_claude(
        json.dumps({"is_error": True, "api_error_status": 529, "error": "Service temporarily busy"}), "", 1
    )
    assert not v6.ok
    assert v6.kind == ErrorKind.SERVER


def test_r1_rate_limited_vs_quota_exhaustion() -> None:
    # Generic rate throttle -> RATE_LIMITED
    v_rate = classify_codex("", "Rate limit reached. Please slow down.", 1)
    assert not v_rate.ok
    assert v_rate.kind == ErrorKind.RATE_LIMITED

    # HTTP 429 without explicit quota -> RATE_LIMITED
    v_429 = classify_gemini(json.dumps({"error": {"code": 429, "message": "Too many requests"}}), "", 1)
    assert not v_429.ok
    assert v_429.kind == ErrorKind.RATE_LIMITED

    # Generic resets not quota -> UNKNOWN when no rate or quota keywords
    v_reset_only = classify_claude("", "Connection closed. Resets in 5 minutes.", 1)
    assert not v_reset_only.ok
    assert v_reset_only.kind == ErrorKind.UNKNOWN
    assert v_reset_only.kind != ErrorKind.LIMIT_REACHED

    # Explicit quota exhaustion remains LIMIT_REACHED even with 429
    v_quota_429 = classify_gemini(
        json.dumps({"error": {"code": 429, "message": "Quota exceeded for current plan"}}), "", 1
    )
    assert not v_quota_429.ok
    assert v_quota_429.kind == ErrorKind.LIMIT_REACHED

    v_insufficient = classify_codex("", "Error: rate limit exceeded (insufficient_quota).", 1)
    assert not v_insufficient.ok
    assert v_insufficient.kind == ErrorKind.LIMIT_REACHED

    v_individual = classify_agy(
        "",
        "AGY_ERROR: {\"short_error\": \"Individual quota reached.\", \"error_code\": 429}",
        1,
    )
    assert not v_individual.ok
    assert v_individual.kind == ErrorKind.LIMIT_REACHED

    # RESOURCE_EXHAUSTED is overloaded across APIs and is not enough by itself to
    # prove account quota exhaustion. A 429 still means a short rate-limit cooldown.
    v_resource = classify_agy(
        "",
        'AGY_ERROR: {"status": "RESOURCE_EXHAUSTED", "short_error": "Request temporarily unavailable."}',
        1,
    )
    assert v_resource.kind == ErrorKind.UNKNOWN

    v_resource_429 = classify_agy(
        "",
        'AGY_ERROR: {"status": "RESOURCE_EXHAUSTED", "error_code": 429, "short_error": "Please retry."}',
        1,
    )
    assert v_resource_429.kind == ErrorKind.RATE_LIMITED

    # A vague usage-limit phrase and reset time do not establish exhausted quota.
    assert classify_codex("", "Usage limit encountered. Resets in 5 hours.", 1).kind == ErrorKind.UNKNOWN

    # Explicit credit exhaustion and scoped plan usage exhaustion remain hard limits.
    assert classify_codex("", "Your credits are exhausted.", 1).kind == ErrorKind.LIMIT_REACHED
    assert classify_codex("", "Your plan usage limit exceeded.", 1).kind == ErrorKind.LIMIT_REACHED


def test_r1_unstructured_does_not_classify_stdout_prose_on_nonzero_exit() -> None:
    prose_with_triggers = (
        "Here is the code to handle HTTP 401 unauthorized and quota exceeded:\n"
        "Please log in using your API key. If the rate limit is hit, wait for it to reset."
    )
    # On nonzero exit with empty stderr, unstructured must NOT scan stdout for auth/quota
    for cli in ("claude", "codex", "agy", "gemini", "hermes"):
        if cli == "claude":
            v = classify_claude(prose_with_triggers, "", 1)
        elif cli == "codex":
            v = classify_codex(prose_with_triggers, "", 1)
        elif cli == "gemini":
            v = classify_gemini(prose_with_triggers, "", 1)
        elif cli == "agy":
            v = classify_agy(prose_with_triggers, "", 1)
        else:
            v = classify_hermes(prose_with_triggers, "", 1, {})
        assert not v.ok
        assert v.kind == ErrorKind.UNKNOWN, f"{cli} classified stdout prose as {v.kind}"
        assert v.kind not in (ErrorKind.NEEDS_LOGIN, ErrorKind.LIMIT_REACHED, ErrorKind.RATE_LIMITED)


def test_r1_codex_final_terminal_event_ordering() -> None:
    # Turn completed earlier, but final terminal event is turn.failed -> FAILURE
    events_fail = [
        {"type": "item.completed", "item": {"text": "Step 1 done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 10}},
        {"type": "item.completed", "item": {"text": "Step 2 failed"}},
        {"type": "turn.failed", "error": {"message": "Rate limit reached during step 2"}},
    ]
    stdout_fail = "\n".join(json.dumps(e) for e in events_fail)
    v_fail = classify_codex(stdout_fail, "", 0)
    assert not v_fail.ok
    assert v_fail.kind == ErrorKind.RATE_LIMITED
    assert "Rate limit reached" in (v_fail.message or "")

    # Turn failed earlier, but retried and final terminal event is turn.completed -> SUCCESS
    events_succeed = [
        {"type": "turn.failed", "error": {"message": "Temporary error"}},
        {"type": "item.completed", "item": {"text": "Step 1 retried and completed"}},
        {"type": "turn.completed", "usage": {"input_tokens": 20}},
    ]
    stdout_succeed = "\n".join(json.dumps(e) for e in events_succeed)
    v_succeed = classify_codex(stdout_succeed, "", 0)
    assert v_succeed.ok

    # Assistant prose containing quota phrases is not failure evidence.
    assistant_limit_text = [
        {"type": "item.completed", "item": {"type": "message", "text": "The API says quota exceeded."}},
        {"type": "turn.completed", "usage": {}},
    ]
    assert classify_codex("\n".join(json.dumps(e) for e in assistant_limit_text), "", 0).ok


@pytest.mark.parametrize("apostrophe", ["'", "\u2019"])
@pytest.mark.parametrize("structured", [False, True])
def test_r1_observed_codex_usage_limit_remains_exhaustion(apostrophe: str, structured: bool) -> None:
    message = (
        f"You{apostrophe}ve hit your usage limit. Upgrade to Pro or "
        "try again at 2026-11-06T04:52:00Z."
    )
    stdout = json.dumps({"type": "turn.failed", "error": {"message": message}}) if structured else ""
    verdict = classify_codex(stdout, "" if structured else message, 1)
    assert not verdict.ok
    assert verdict.kind == ErrorKind.LIMIT_REACHED
    assert verdict.reset_at == datetime(2026, 11, 6, 4, 52, tzinfo=UTC)


def test_r1_codex_time_only_reset_stays_unknown() -> None:
    # The CLI's display timezone/date is absent: recognize the limit without inventing a reset date.
    verdict = classify_codex("", "You've hit your usage limit. Try again at 4:52 AM.", 1)
    assert verdict.kind == ErrorKind.LIMIT_REACHED
    assert verdict.reset_at is None


@pytest.mark.parametrize("stale_stderr", ["", "Earlier request: quota exceeded"])
def test_r1_codex_success_boundary_discards_stale_failure(stale_stderr: str) -> None:
    events = [
        {"type": "turn.failed", "error": {"message": "Quota exceeded"}},
        {"type": "turn.completed", "usage": {}},
        {"type": "turn.failed", "error": {"message": "Connection dropped"}},
    ]
    verdict = classify_codex("\n".join(json.dumps(event) for event in events), stale_stderr, 1)
    assert not verdict.ok
    assert verdict.kind == ErrorKind.UNKNOWN
    assert "quota exceeded" not in (verdict.message or "").lower()


def test_r1_codex_actual_quota_after_success_still_exhausts() -> None:
    events = [
        {"type": "turn.failed", "error": {"message": "Connection dropped"}},
        {"type": "turn.completed", "usage": {}},
        {"type": "turn.failed", "error": {"message": "You've hit your usage limit. Resets in 5h."}},
    ]
    verdict = classify_codex("\n".join(json.dumps(event) for event in events), "", 1)
    assert not verdict.ok
    assert verdict.kind == ErrorKind.LIMIT_REACHED
    assert verdict.reset_at is not None
    assert "Connection dropped" not in (verdict.message or "")


def test_explicit_claude_usage_limit_reached_keeps_provider_reset() -> None:
    payload = json.dumps({
        "result": "Usage limit reached. Your limit will reset at 2026-11-06T00:00:00Z",
        "is_error": True,
    })
    verdict = classify_claude(payload, "", 1)
    assert verdict.kind == ErrorKind.LIMIT_REACHED
    assert verdict.reset_at is not None
    assert verdict.reset_at.isoformat() == "2026-11-06T00:00:00+00:00"


@pytest.mark.parametrize("stderr", ["error at line 429 of foo.py", "processed 429 records"])
def test_bare_429_is_not_account_throttling(stderr: str) -> None:
    assert classify_claude("", stderr, 1).kind == ErrorKind.UNKNOWN


@pytest.mark.parametrize("stderr", ["HTTP 429", "HTTP/1.1 429", "status code: 429", "error 429"])
def test_explicit_http_429_remains_rate_limited(stderr: str) -> None:
    assert classify_claude("", stderr, 1).kind == ErrorKind.RATE_LIMITED
