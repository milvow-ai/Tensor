"""farm.secrets: auth_ref resolution never leaks a value; redact masks everything it should."""

from __future__ import annotations

import logging
from urllib.parse import quote, quote_plus

import pytest

from farm.secrets import (
    MASK,
    AuthRefError,
    RedactingFilter,
    install_log_redaction,
    parse_auth_ref,
    redact,
    register_secret,
    resolve_auth,
)

SECRET = "SENTINEL-secret-4f9c2a71"


# --- resolve_auth ------------------------------------------------------------------------------------


def test_env_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_TEST_KEY", SECRET)
    assert resolve_auth("env:FARM_TEST_KEY") == SECRET


def test_env_value_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_TEST_KEY", f"  {SECRET}\r\n")
    assert resolve_auth("env:FARM_TEST_KEY") == SECRET


def test_missing_env_names_the_variable_but_holds_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FARM_TEST_MISSING", raising=False)
    monkeypatch.setenv("FARM_TEST_OTHER", SECRET)  # a value exists in the environment, just not this one
    with pytest.raises(AuthRefError) as caught:
        resolve_auth("env:FARM_TEST_MISSING")
    message = str(caught.value)
    assert "FARM_TEST_MISSING" in message
    assert SECRET not in message


@pytest.mark.parametrize("blank", ["", "   ", "\r\n"])
def test_empty_env_value_is_an_error(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("FARM_TEST_EMPTY", blank)
    with pytest.raises(AuthRefError, match="FARM_TEST_EMPTY"):
        resolve_auth("env:FARM_TEST_EMPTY")


@pytest.mark.parametrize(
    "bad_ref",
    [
        "",
        ":",
        "env:",
        "plainvalue-NOTAREF-marker",  # someone pasted a raw key instead of a reference
        "env:has-dash-NOTAREF-marker",
        "env:1STARTS_WITH_DIGIT",
        "unknown:NOTAREF-marker",
        "token-store:",
        "cli:bad value NOTAREF-marker",
    ],
)
def test_malformed_auth_ref_is_rejected_without_echoing_it(bad_ref: str) -> None:
    with pytest.raises(AuthRefError) as caught:
        resolve_auth(bad_ref)
    assert "NOTAREF-marker" not in str(caught.value)
    assert "1STARTS_WITH_DIGIT" not in str(caught.value)


@pytest.mark.parametrize("ref", ["token-store:clay-01", "cli:claude-02"])
def test_schemes_that_arrive_later_say_so(ref: str) -> None:
    scheme = ref.split(":")[0]
    assert parse_auth_ref(ref) == (scheme, ref.split(":")[1])
    with pytest.raises(AuthRefError, match="not supported yet") as caught:
        resolve_auth(ref)
    assert scheme in str(caught.value)


def test_parse_auth_ref_accepts_the_three_schemes() -> None:
    assert parse_auth_ref("env:REOON_API_KEY") == ("env", "REOON_API_KEY")
    assert parse_auth_ref("token-store:clay-01") == ("token-store", "clay-01")
    assert parse_auth_ref("cli:farm-agent") == ("cli", "farm-agent")


# --- redact ------------------------------------------------------------------------------------------


def test_redact_masks_a_resolved_key_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_TEST_KEY", SECRET)
    resolve_auth("env:FARM_TEST_KEY")
    text = f"call failed for {SECRET}; again {SECRET}!"
    assert SECRET not in redact(text)
    assert redact(text) == f"call failed for {MASK}; again {MASK}!"


def test_redact_masks_a_key_in_a_url_and_keeps_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FARM_TEST_KEY", SECRET)
    resolve_auth("env:FARM_TEST_KEY")
    base = "https://emailverifier.reoon.com/api/v1/verify?email=jane%40example.com"
    cleaned = redact(f"GET {base}&key={SECRET}&mode=power -> 500")
    assert SECRET not in cleaned
    assert cleaned == f"GET {base}&key={MASK}&mode=power -> 500"


def test_redact_masks_unseen_values_of_secret_looking_query_params() -> None:
    # Never resolved in this process (say, a key another component handled): the parameter name gives it away.
    url = "https://x.example/v1?email=a@b.com&api_key=zzTOPSECRETzz&token=t0k3n&access_token=abc123&apikey=k"
    cleaned = redact(url)
    for leaked in ("zzTOPSECRETzz", "t0k3n", "abc123"):
        assert leaked not in cleaned
    assert "email=a@b.com" in cleaned
    assert cleaned.count(MASK) == 4


@pytest.mark.parametrize("param", ["key", "KEY", "Api_Key", "api-key", "apikey", "Token", "ACCESS_TOKEN"])
def test_redact_param_names_are_case_insensitive(param: str) -> None:
    assert redact(f"?{param}=hunter2xyz&x=1") == f"?{param}={MASK}&x=1"


def test_redact_leaves_lookalike_names_and_plain_text_alone() -> None:
    assert redact("monkey=banana keyboard=qwerty") == "monkey=banana keyboard=qwerty"
    assert redact("the key was rotated") == "the key was rotated"
    assert redact("email=jane@example.com&mode=power") == "email=jane@example.com&mode=power"
    assert redact("") == ""


def test_redact_masks_long_bearer_tokens_only() -> None:
    assert redact("Authorization: Bearer abcdefghijklmnop1234567890") == f"Authorization: Bearer {MASK}"
    assert redact("a bearer of bad news") == "a bearer of bad news"


def test_redact_masks_percent_encoded_forms_of_a_resolved_key(monkeypatch: pytest.MonkeyPatch) -> None:
    awkward = "ab cd/ef+gh=ij&kl"
    monkeypatch.setenv("FARM_TEST_AWKWARD", awkward)
    resolve_auth("env:FARM_TEST_AWKWARD")
    for form in (awkward, quote(awkward, safe=""), quote_plus(awkward)):
        assert redact(f"before {form} after") == f"before {MASK} after"


def test_redact_prefers_the_longest_known_secret() -> None:
    register_secret("LONGPREFIX-abcd")
    register_secret("LONGPREFIX-abcd-with-a-tail")
    assert redact("x LONGPREFIX-abcd-with-a-tail y") == f"x {MASK} y"


def test_very_short_values_are_not_registered() -> None:
    register_secret("ab")
    assert redact("a lab about abs") == "a lab about abs"


# --- log redaction -----------------------------------------------------------------------------------


def test_log_filter_masks_secrets_in_formatted_records(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FARM_TEST_KEY", SECRET)
    resolve_auth("env:FARM_TEST_KEY")
    name = "farm.test.redaction"
    install_log_redaction(name)
    install_log_redaction(name)  # idempotent
    logger = logging.getLogger(name)
    assert sum(isinstance(f, RedactingFilter) for f in logger.filters) == 1

    with caplog.at_level(logging.INFO, logger=name):
        logger.info("HTTP Request: %s %s", "GET", f"https://x.example/v1?key={SECRET}&email=a@b.com")
    assert "HTTP Request: GET https://x.example/v1" in caplog.text
    assert SECRET not in caplog.text
    assert "email=a@b.com" in caplog.text


def test_log_filter_never_drops_a_record_even_with_a_broken_format_string() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "needs %s %s", ("one",), None)
    assert RedactingFilter().filter(record) is True
