"""Input/output models and helpers added for the M2c capabilities (farm/capabilities/schemas.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from farm.capabilities.schemas import (
    CAPABILITY_MODELS,
    ClassifyIn,
    EnrichCompanyIn,
    ExtractIn,
    FindEmailIn,
    FindEmailOut,
    FindPersonIn,
    JobsLookupIn,
    PageSpeedIn,
    Source,
    check_json_schema,
    employee_size_range,
    json_schema_errors,
    map_hunter_status,
    normalise_domain,
    normalise_http_url,
)

NOW = datetime(2026, 10, 4, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme.com", "acme.com"),
        ("  ACME.com  ", "acme.com"),
        ("www.acme.com", "acme.com"),
        ("https://www.Acme.com/about?x=1#top", "acme.com"),
        ("http://acme.com", "acme.com"),
        ("acme.com/", "acme.com"),
        ("sub.acme.co.uk", "sub.acme.co.uk"),
        ("acme.com.", "acme.com"),
        ("bücher.example", "xn--bcher-kva.example"),
    ],
)
def test_normalise_domain(raw: str, expected: str) -> None:
    assert normalise_domain(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "acme",
        "a@acme.com",
        "acme.com:8080",
        "1.2.3.4",
        "ac me.com",
        "-acme.com",
        "a" * 64 + ".com",
        "..com",
    ],
)
def test_normalise_domain_rejects_what_is_not_a_domain(bad: str) -> None:
    with pytest.raises(ValueError, match=r"domain|hostname"):
        normalise_domain(bad)


@pytest.mark.parametrize(
    "ok", ["http://a.example", "https://a.example/p?q=1", "https://localhost:8080/", " https://a.example "]
)
def test_normalise_http_url_accepts_absolute_http_urls(ok: str) -> None:
    assert normalise_http_url(ok) == ok.strip()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "a.example",
        "//a.example",
        "ftp://a.example",
        "file:///etc/passwd",
        "https://",
        "https://u:p@a.example/",
        "mailto:a@b.c",
    ],
)
def test_normalise_http_url_rejects_the_rest(bad: str) -> None:
    with pytest.raises(ValueError):
        normalise_http_url(bad)


@pytest.mark.parametrize(
    ("count", "bucket"),
    [
        (None, None),
        (0, None),
        (-5, None),
        (1, "1-10"),
        (10, "1-10"),
        (11, "11-50"),
        (50, "11-50"),
        (51, "51-200"),
        (200, "51-200"),
        (201, "201-500"),
        (500, "201-500"),
        (501, "501-1000"),
        (1000, "501-1000"),
        (1001, "1001-5000"),
        (1600, "1001-5000"),
        (5000, "1001-5000"),
        (5001, "5001-10000"),
        (10000, "5001-10000"),
        (10001, "10001+"),
        (2_000_000, "10001+"),
    ],
)
def test_employee_size_range_buckets(count: int | None, bucket: str | None) -> None:
    assert employee_size_range(count) == bucket


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("valid", ("valid", None)),
        ("INVALID", ("invalid", None)),
        ("accept_all", ("catch_all", None)),
        ("accept-all", ("catch_all", None)),
        ("webmail", ("risky", "mailbox_unchecked")),
        ("disposable", ("risky", "disposable")),
        ("unknown", ("unknown", None)),
        ("deliverable", None),
        ("", None),
        (None, None),
    ],
)
def test_map_hunter_status(status: object, expected: tuple[str, str | None] | None) -> None:
    assert map_hunter_status(status) == expected


# --- input models ------------------------------------------------------------------------------------------


def test_find_person_in_needs_a_company_and_trims_everything() -> None:
    assert FindPersonIn(company_name="  Acme  ").company_name == "Acme"
    model = FindPersonIn(company_domain="https://www.acme.com", titles=[" CEO "], seniority=["vp"], limit=5)
    assert (model.company_domain, model.titles, model.limit) == ("acme.com", ["CEO"], 5)
    for bad in ({}, {"titles": ["x"]}, {"company_name": " "}):
        with pytest.raises(ValidationError):
            FindPersonIn(**bad)


def test_inputs_reject_unknown_fields() -> None:
    for model, params in [
        (FindPersonIn, {"company_name": "A", "typo": 1}),
        (FindEmailIn, {"first_name": "A", "last_name": "B", "domain": "a.io", "typo": 1}),
        (EnrichCompanyIn, {"domain": "a.io", "typo": 1}),
        (PageSpeedIn, {"url": "https://a.io", "typo": 1}),
        (JobsLookupIn, {"board": "a", "typo": 1}),
        (ClassifyIn, {"text": "t", "labels": ["a", "b"], "typo": 1}),
    ]:
        with pytest.raises(ValidationError, match="Extra inputs"):
            model(**params)


def test_find_email_in_normalises_the_domain() -> None:
    model = FindEmailIn(first_name=" Jane ", last_name="Doe", domain="HTTPS://WWW.Acme.COM/team")
    assert (model.first_name, model.domain) == ("Jane", "acme.com")


def test_jobs_lookup_in_rules() -> None:
    assert JobsLookupIn(board="stripe").country == "us"
    assert JobsLookupIn(what="dev", country=" GB ").country == "gb"
    assert JobsLookupIn(company="Acme", ats="lever").ats == "lever"
    for bad in (
        {},
        {"country": "gb"},
        {"ats": "lever"},
        {"what": "x", "ats": "lever"},
        {"board": "a b"},
        {"board": "-a"},
    ):
        with pytest.raises(ValidationError):
            JobsLookupIn(**bad)


# --- JSON Schema helpers -----------------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
    "required": ["name"],
}


def test_json_schema_errors_are_short_and_located() -> None:
    assert json_schema_errors({"name": "x", "tags": ["a"]}, SCHEMA) == []
    problems = json_schema_errors({"tags": ["a", 2, "c"]}, SCHEMA)
    assert any(p.startswith("<root>: 'name' is a required property") for p in problems)
    assert any(p.startswith("tags: ") and "too long" in p for p in problems)
    assert any(p.startswith("tags/1: ") for p in problems)


def test_json_schema_errors_are_bounded() -> None:
    schema = {"type": "object", "properties": {f"k{n}": {"type": "integer"} for n in range(20)}}
    problems = json_schema_errors({f"k{n}": "x" for n in range(20)}, schema, limit=3)
    assert len(problems) == 3


def test_check_json_schema() -> None:
    assert check_json_schema(SCHEMA) is SCHEMA
    for bad, needle in [
        ({"type": "object", "properties": {"a": {"type": "nope"}}}, "not a valid JSON Schema"),
        ({"type": "array"}, "JSON object"),
        ({"properties": {}}, "JSON object"),
        ({"type": "object", "properties": {"a": {"$ref": "https://x.example/s.json"}}}, r"local \$ref"),
        ({"type": "object", "properties": {"a": {"$ref": "other.json#/a"}}}, r"local \$ref"),
        ({"type": "object", "x": "y" * 20_001}, "20,000"),
    ]:
        with pytest.raises(ValueError, match=needle):
            check_json_schema(bad)
    local = {"type": "object", "properties": {"a": {"$ref": "#/$defs/b"}}, "$defs": {"b": {"type": "string"}}}
    assert check_json_schema(local) is local
    deep = {
        "type": "object",
        "properties": {
            "a": {"type": "array", "items": {"anyOf": [{"$ref": "http://x.example"}, {"type": "string"}]}}
        },
    }
    with pytest.raises(ValueError, match="local"):
        check_json_schema(deep)  # a remote ref hidden inside a list is found too


def test_extract_in_accepts_a_valid_schema_and_keeps_it_verbatim() -> None:
    model = ExtractIn(text="t", json_schema=SCHEMA)
    assert model.json_schema == SCHEMA


# --- the registry of models --------------------------------------------------------------------------------


def test_capability_models_pair_inputs_with_outputs() -> None:
    assert set(CAPABILITY_MODELS) == {
        "verify_email",
        "find_person",
        "find_email",
        "enrich_company",
        "pagespeed",
        "jobs_lookup",
        "extract",
        "classify",
    }
    for name, (model_in, model_out) in CAPABILITY_MODELS.items():
        assert model_in.__name__.endswith("In") and model_out.__name__.endswith("Out"), name


def test_every_new_output_carries_source_and_observed_at() -> None:
    for name, (_, model_out) in CAPABILITY_MODELS.items():
        if name == "verify_email":
            continue  # the M1b model predates this rule and carries provider/checked_at
        fields = model_out.model_fields
        assert "source" in fields and "observed_at" in fields, name


def test_output_observed_at_must_be_timezone_aware() -> None:
    source = Source(provider="hunter", connection_id="hunter-01")
    FindEmailOut(source=source, observed_at=NOW, first_name="A", last_name="B", domain="a.io")
    with pytest.raises(ValidationError):
        FindEmailOut(
            source=source, observed_at=datetime(2026, 10, 4), first_name="A", last_name="B", domain="a.io"
        )  # noqa: DTZ001


def test_confidence_is_a_percentage() -> None:
    source = Source(provider="hunter", connection_id="hunter-01")
    base: dict[str, Any] = {
        "source": source,
        "observed_at": NOW,
        "first_name": "A",
        "last_name": "B",
        "domain": "a.io",
    }
    assert FindEmailOut(**base, confidence=100).confidence == 100
    for bad in (-1, 101):
        with pytest.raises(ValidationError):
            FindEmailOut(**base, confidence=bad)
