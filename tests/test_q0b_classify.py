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
