"""Outcome classification for the AI CLI agents (briefs/Q0B-classify.md; docs/QUALITY.md D1, D4).

One rule order for every CLI:

1. **Structured success** (the CLI's own result object says it worked) -> ok. Nothing else is read:
   the answer text and stderr of a successful run are never scanned for error words.
2. **Structured failure** -> the error kind comes from the structured error fields
   (status / HTTP code / error text) and stderr, through that CLI's tables of exact phrases: login
   (``NEEDS_LOGIN``), a request the CLI refuses as given, such as a model it does not know
   (``BAD_REQUEST``: the account is fine), and limits (``LIMIT_REACHED``), checked in that order.
3. **No structured output** -> exit code and stderr only (exit 0 is a success).
4. Anything the tables do not recognise -> ``ErrorKind.UNKNOWN`` (never a guess such as ``NEEDS_LOGIN``).

Phrases in the tables come from real outputs (``tests/fixtures/cli/``) or the CLIs' documented messages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from farm.executors.base import ErrorKind
from farm.executors.cli_agent.base import (
    clean_cli_text,
    parse_json_or_none,
    parse_jsonl,
    parse_reset_at,
)


@dataclass(frozen=True)
class Classification:
    """Verdict on one CLI run. ``kind`` and ``message`` are set only when ``ok`` is false."""

    ok: bool
    kind: ErrorKind | None = None
    message: str | None = None
    reset_at: datetime | None = None


_OK = Classification(ok=True)

# --- phrase tables (matched case-insensitively, only against error channels) -----------------------

_AUTH_COMMON = (
    r"not logged in",
    r"please log in",
    r"authentication required",
    r"invalid api key",
    r"token expired",
    r"unauthorized",
    r"\bhttp 401\b",
)
_LIMIT_COMMON = (
    r"usage limit",
    r"rate limit",
    r"quota exceeded",
    r"exceeded your current quota",
    r"insufficient_quota",
    r"insufficient balance",
    r"insufficient funds",
    r"overloaded",
    r"resource_exhausted",
    r"individual quota reached",
    r"\bresets?\s+(?:at|in)\b",
    r"\bhttp 429\b",
)

_TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "claude": (
        _AUTH_COMMON
        + (
            r"please run /login",
            r"claude login",
            r"setup-token",
            r"invalid authentication credentials",
            # "Failed to authenticate: OAuth session expired and could not be refreshed". Not the transient
            # "Failed to refresh OAuth token: another Claude Code process is refreshing it ...".
            r"oauth session expired",
        ),
        _LIMIT_COMMON + (r"at capacity",),
    ),
    "codex": (_AUTH_COMMON + (r"codex login",), _LIMIT_COMMON),
    "agy": (
        _AUTH_COMMON
        + (r"not eligible for antigravity", r"eligibility check failed", r"launch agy interactively"),
        _LIMIT_COMMON + (r"model capacity",),
    ),
    "gemini": (_AUTH_COMMON + (r"gemini auth", r"login with google"), _LIMIT_COMMON + (r"model capacity",)),
    "hermes": (_AUTH_COMMON + (r"rejected your api key", r"hermes setup"), _LIMIT_COMMON),
}
# The CLI refused the request as given (BAD_REQUEST: neither the account's health nor its state changes).
_REQUEST: dict[str, tuple[str, ...]] = {
    # The CLI's own message for a model the API does not know or the account cannot use.
    "claude": (r"issue with the selected model",),
    # agy checks --model against its own list before any call: "invalid model selection (--model "x" ...):
    # model x is not recognized as a known model or custom model in settings" (no tokens used).
    "agy": (r"invalid model selection", r"is not recognized as a known model"),
}
_COMPILED = {
    cli: (
        [re.compile(p, re.IGNORECASE) for p in auth],
        [re.compile(p, re.IGNORECASE) for p in _REQUEST.get(cli, ())],
        [re.compile(p, re.IGNORECASE) for p in limit],
    )
    for cli, (auth, limit) in _TABLES.items()
}


def _kind_from_text(cli: str, text: str) -> ErrorKind | None:
    auth, request, limit = _COMPILED[cli]
    if any(p.search(text) for p in auth):
        return ErrorKind.NEEDS_LOGIN
    if any(p.search(text) for p in request):
        return ErrorKind.BAD_REQUEST
    if any(p.search(text) for p in limit):
        return ErrorKind.LIMIT_REACHED
    return None


def _kind_from_http(code: Any) -> ErrorKind | None:
    """Structured HTTP-style code: 401 = bad login, 429 = limit; anything else says nothing."""
    try:
        n = int(code)
    except (TypeError, ValueError):
        return None
    if n == 401:
        return ErrorKind.NEEDS_LOGIN
    if n == 429:
        return ErrorKind.LIMIT_REACHED
    return None


# --- reset times -----------------------------------------------------------------------------------

_COMPACT_RESET = re.compile(r"resets?\s+in\s+((?:\d+\s*[dhms](?![a-z])\s*)+)", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_RESET_KEYS = ("reset_at", "next_reset_at", "resets_at", "reset_time")


def parse_reset(
    text: str, data: dict[str, Any] | None = None, now: datetime | None = None
) -> datetime | None:
    """Reset timestamp from a payload or error text; also reads "Resets in 91h35m7s"."""
    m = _COMPACT_RESET.search(text)
    if m and not (data and any(k in data for k in _RESET_KEYS)):
        parts = re.findall(r"(\d+)\s*([dhms])", m.group(1), re.IGNORECASE)
        seconds = sum(int(n) * _UNIT_SECONDS[u.lower()] for n, u in parts)
        return (now or datetime.now(UTC)) + timedelta(seconds=seconds)
    return parse_reset_at(text, data)


# --- helpers ---------------------------------------------------------------------------------------


_ERROR_PREFIX = re.compile(r"^error:\s*", re.IGNORECASE)


def _join_error_texts(texts: Iterable[str]) -> str:
    """The cleaned error-channel texts, joined. A text that an earlier one already holds is kept once (agy
    puts its error in the JSON and prints it again on stderr as ``error: ...``)."""
    parts: list[str] = []
    for raw in texts:
        text = clean_cli_text(raw)
        if text and not any(_ERROR_PREFIX.sub("", text) in part for part in parts):
            parts.append(text)
    return "\n".join(parts)


def _failure(
    cli: str, kind_hint: ErrorKind | None, texts: Iterable[str], fallback: str, data: dict[str, Any] | None
) -> Classification:
    """Build a failure from the error-channel ``texts`` (joined) with an optional structured ``kind_hint``."""
    text = _join_error_texts(texts)
    kind = kind_hint or _kind_from_text(cli, text) or ErrorKind.UNKNOWN
    reset_at = parse_reset(text, data) if kind == ErrorKind.LIMIT_REACHED else None
    return Classification(ok=False, kind=kind, message=text or fallback, reset_at=reset_at)


def _unstructured(cli: str, stdout: str, stderr: str, exit_code: int) -> Classification:
    """Rule 3: no structured output. Exit 0 is success; otherwise stderr decides."""
    if exit_code == 0:
        return _OK
    kind = _kind_from_text(cli, clean_cli_text(stderr))
    return _failure(cli, kind, [stderr or stdout], f"{cli} exited with code {exit_code}", None)


_AGY_ERROR_LINE = re.compile(r"^AGY_ERROR:\s*(\{.*\})\s*$", re.MULTILINE)


def _agy_stderr_error(stderr: str) -> dict[str, Any] | None:
    """The structured ``AGY_ERROR: {...}`` line agy prints on stderr when a run fails."""
    m = _AGY_ERROR_LINE.search(stderr)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


# --- per-CLI classifiers ---------------------------------------------------------------------------


def classify_claude(stdout: str, stderr: str, exit_code: int) -> Classification:
    data = parse_json_or_none(stdout)
    if not isinstance(data, dict):
        return _unstructured("claude", stdout, stderr, exit_code)
    if not data.get("is_error", exit_code != 0):
        return _OK
    hint = _kind_from_http(data.get("api_error_status"))
    texts = [str(data.get("result") or data.get("error") or ""), stderr]
    return _failure("claude", hint, texts, "Claude execution error", data)


def classify_agy(stdout: str, stderr: str, exit_code: int) -> Classification:
    data = parse_json_or_none(stdout)
    if isinstance(data, dict) and ("status" in data or "response" in data or "error" in data):
        status = str(data.get("status") or "")
        if status == "SUCCESS" or (not status and "error" not in data and exit_code == 0):
            return _OK
        hint = _kind_from_http(data.get("error_code"))
        texts = [str(data.get("short_error") or data.get("error") or data.get("message") or ""), stderr]
        return _failure("agy", hint, texts, f"agy status: {status or 'error'}", data)
    err = _agy_stderr_error(stderr)
    if err:
        hint = _kind_from_http(err.get("error_code"))
        if hint is None and str(err.get("status")) == "RESOURCE_EXHAUSTED":
            hint = ErrorKind.LIMIT_REACHED
        return _failure("agy", hint, [stderr], "agy error", err)
    return _unstructured("agy", stdout, stderr, exit_code)


def classify_gemini(stdout: str, stderr: str, exit_code: int) -> Classification:
    """Gemini CLI headless JSON: ``{"response": ...}`` on success, ``{"error": {...}}`` on failure."""
    data = parse_json_or_none(stdout)
    if isinstance(data, dict) and ("response" in data or "error" in data):
        err = data.get("error")
        if not err:
            return _OK
        if isinstance(err, dict):
            hint = _kind_from_http(err.get("code"))
            texts = [str(err.get("message") or ""), stderr]
        else:
            hint, texts = None, [str(err), stderr]
        return _failure("gemini", hint, texts, "Gemini CLI error", data)
    return _unstructured("gemini", stdout, stderr, exit_code)


_CODEX_DONE = {"turn.completed", "turn.finished"}
_CODEX_FAIL = {"error", "turn.failed"}


def _codex_error_text(ev: dict[str, Any]) -> str:
    err = ev.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")
    return str(ev.get("message") or err or "")


def classify_codex(stdout: str, stderr: str, exit_code: int) -> Classification:
    # A successful run's stderr holds Codex log noise ("ERROR codex_core::session: ..."): not read.
    events = parse_jsonl(stdout)
    if any(str(ev.get("type", "")) in _CODEX_DONE for ev in events):
        return _OK
    single = parse_json_or_none(stdout)
    if isinstance(single, dict) and any(k in single for k in ("result", "response", "text")):
        return _OK
    failures = [_codex_error_text(ev) for ev in events if str(ev.get("type", "")) in _CODEX_FAIL]
    if failures:
        return _failure("codex", None, [*failures, stderr], "Codex error", None)
    return _unstructured("codex", stdout, stderr, exit_code)


def classify_hermes(stdout: str, stderr: str, exit_code: int, usage: dict[str, Any] | None) -> Classification:
    usage = usage or {}
    if usage.get("failed"):
        # Hermes prints its error message on stdout and flags the run failed in the usage file.
        return _failure("hermes", None, [stdout, stderr], "Hermes execution failed", usage)
    if exit_code != 0:
        return _unstructured("hermes", stdout, stderr, exit_code)
    if usage.get("interrupted") and not stdout.strip():
        return Classification(ok=False, kind=ErrorKind.UNKNOWN, message="Hermes run was interrupted")
    return _OK
