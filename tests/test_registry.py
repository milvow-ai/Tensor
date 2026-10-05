"""Registry: the real YAML validates; bad input fails loudly and clearly; the JSON Schema is usable."""

from __future__ import annotations

import copy
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from farm.registry import Registry, RegistryError, export_json_schema, load_registry, parse_registry
from farm.registry.models import STRATEGIES
from farm.secrets import parse_auth_ref

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "config" / "registry.yaml"
EXAMPLE = ROOT / "config" / "registry.example.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "registry.yaml"


def fixture_data() -> dict[str, Any]:
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return copy.deepcopy(data)


def load_data(data: dict[str, Any]) -> Registry:
    return parse_registry(yaml.safe_dump(data), source="test.yaml")


def expect_error(data: dict[str, Any], *fragments: str) -> str:
    with pytest.raises(RegistryError) as caught:
        load_data(data)
    message = str(caught.value)
    for fragment in fragments:
        assert fragment in message, f"{fragment!r} not in:\n{message}"
    return message


# --- the real and the fixture registry ---------------------------------------------------------------


def test_blank_owner_registry_loads() -> None:
    reg = load_registry(REAL)
    assert len(reg.providers) == 0
    assert set(reg.capabilities) == {"ask_ai", "agent_task"}
    assert reg.capabilities["ask_ai"].routes == []
    assert reg.capabilities["agent_task"].routes == []
    assert reg.settings.owner_email == "milvow.ai@gmail.com"
    assert reg.settings.alert_thresholds == [50, 80, 100]
    assert reg.budgets.global_monthly_usd == 0


def test_example_registry_loads_and_matches_the_brief() -> None:
    reg = load_registry(EXAMPLE)
    assert list(reg.providers) == [
        "reoon",
        "zerobounce",
        "apollo",
        "hunter",
        "pagespeed",
        "adzuna",
        "ats_public",
        "llm",
        "clay",
        "claude",
        "gemini",
        "codex",
        "hermes",
    ]
    assert reg.settings.owner_email == "milvow.ai@gmail.com"
    assert reg.settings.alert_thresholds == [50, 80, 100]
    assert reg.budgets.global_monthly_usd == 0
    assert reg.settings.global_monthly_budget_usd == 0
    assert all(v == 0 for v in reg.budgets.per_provider.values())
    assert set(reg.budgets.per_provider) == set(reg.providers)

    reoon = reg.providers["reoon"].connections[0]
    assert (reoon.id, reoon.auth_ref) == ("reoon-01", "env:REOON_API_KEY")
    assert reoon.units["credits"].limit == 20
    assert (reoon.units["credits"].period, reoon.units["credits"].charged_on) == ("day", "success")

    zb = reg.providers["zerobounce"].connections[0]
    assert (zb.id, zb.auth_ref) == ("zerobounce-01", "env:ZEROBOUNCE_API_KEY")
    assert (zb.units["credits"].limit, zb.units["credits"].period, zb.units["credits"].anchor) == (
        100,
        "month",
        1,
    )

    assert reg.providers["apollo"].connections[0].status == "needs_login"
    assert reg.providers["apollo"].connections[0].auth_ref == "env:APOLLO_API_KEY"
    assert [c.id for c in reg.providers["hunter"].connections] == ["hunter-01"]

    clay = reg.providers["clay"]
    assert (clay.executor, clay.default_strategy) == ("mcp", "sticky")
    assert [c.id for c in clay.connections] == [f"clay-0{n}" for n in range(1, 8)]
    for n, conn in enumerate(clay.connections, start=1):
        assert conn.auth_ref == f"token-store:clay-0{n}"
        assert conn.status == "needs_login"
        assert conn.units["credits"].limit is None

    claude = reg.providers["claude"]
    assert (claude.kind, claude.executor) == ("ai", "cli_agent")
    assert [c.id for c in claude.connections] == ["claude-02", "claude-03", "claude-04"]
    for conn in claude.connections:
        suffix = conn.id.removeprefix("claude-")
        assert conn.auth_ref == f"cli:claude-{suffix}"
        assert conn.status == "needs_login"
        assert conn.meta == {
            "cli": "claude",
            "config_dir": f"D:/farm-data/ai/claude-{suffix}",
            "models": ["sonnet", "opus", "haiku"],
        }
        assert (conn.units["requests"].limit, conn.units["requests"].period) == (None, "rolling_5h")

    agy = reg.providers["gemini"].connections[0]
    assert (agy.id, agy.status) == ("agy-01", "active")
    assert agy.meta == {"cli": "agy", "models": ["gemini-3.8-flash-high", "gemini-3.1-pro-high"]}
    assert reg.providers["codex"].connections[0].status == "needs_login"
    hermes = reg.providers["hermes"].connections[0]
    assert (hermes.id, hermes.status) == ("hermes-01", "active")
    assert hermes.meta == {
        "cli": "hermes",
        "profile": "farm-agent",
        "models": ["openrouter/deepseek/deepseek-v4-flash"],
    }

    verify = reg.capabilities["verify_email"]
    assert (verify.kind, verify.routes, verify.strategy, verify.cache_ttl_seconds) == (
        "tool",
        ["reoon", "zerobounce", "hunter"],  # Hunter is the third verify pool (M2c)
        "failover",
        5184000,
    )
    ask = reg.capabilities["ask_ai"]
    assert (ask.kind, ask.routes, ask.strategy, ask.cache_ttl_seconds) == (
        "ai",
        ["claude", "gemini", "codex", "hermes"],
        "failover",
        0,
    )


def test_real_registry_holds_references_not_secrets() -> None:
    for path in (REAL, EXAMPLE):
        reg = load_registry(path)
        for _provider, conn in reg.iter_connections():
            scheme, _ref = parse_auth_ref(conn.auth_ref)
            assert scheme in {"env", "token-store", "cli"}
        raw = path.read_text(encoding="utf-8")
        assert not re.search(r"sk-[A-Za-z0-9_-]{16,}|[A-Za-z0-9]{32,}", raw)


def test_fixture_registry_loads() -> None:
    reg = load_registry(FIXTURE)
    assert [c.id for c in reg.providers["reoon"].connections] == ["reoon-01", "reoon-02"]
    assert reg.capabilities["verify_email"].routes == ["reoon", "zerobounce"]
    assert reg.providers["clay"].connections[0].units["credits"].unit_cost_usd == Decimal("0.074")


def test_defaults_and_derived_fields() -> None:
    reg = load_registry(FIXTURE)
    zb = reg.providers["zerobounce"]
    conn = zb.connections[0]
    assert conn.label == "zerobounce-01"  # label defaults to the id
    assert (conn.scope, conn.priority, conn.concurrency, conn.strategy) == (["internal"], 100, 1, None)
    assert zb.default_strategy == "failover" and zb.enabled is True
    assert reg.settings.global_monthly_budget_usd == Decimal(50)  # mirrored from budgets

    data = fixture_data()
    del data["providers"]["reoon"]["name"]
    assert load_data(data).providers["reoon"].name == "reoon"  # name defaults to the key


def test_money_and_quantities_are_decimal_in_python_and_numbers_in_json() -> None:
    reg = load_registry(FIXTURE)
    unit = reg.providers["clay"].connections[0].units["credits"]
    assert isinstance(unit.limit, Decimal) and isinstance(unit.unit_cost_usd, Decimal)
    dumped = reg.model_dump(mode="json")
    json_unit = dumped["providers"]["clay"]["connections"][0]["units"]["credits"]
    assert json_unit["unit_cost_usd"] == 0.074 and json_unit["limit"] == 2500
    assert dumped["providers"]["clay"]["connections"][1]["units"]["credits"]["limit"] is None


# --- bad input ---------------------------------------------------------------------------------------


def test_duplicate_connection_id_inside_a_provider() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][1]["id"] = "reoon-01"
    expect_error(data, "connection id 'reoon-01' is used more than once", "provider 'reoon'")


def test_duplicate_connection_id_across_providers() -> None:
    data = fixture_data()
    data["providers"]["zerobounce"]["connections"][0]["id"] = "reoon-01"
    expect_error(data, "connection id 'reoon-01' is used more than once", "'reoon' and 'zerobounce'")


def test_route_must_reference_an_existing_provider() -> None:
    data = fixture_data()
    data["capabilities"]["verify_email"]["routes"] = ["reoon", "mailboxlayer"]
    expect_error(data, "capability 'verify_email'", "route 'mailboxlayer' is not a provider")


def test_capability_kind_must_match_provider_kind() -> None:
    data = fixture_data()
    data["capabilities"]["verify_email"]["routes"] = ["reoon", "claude"]
    expect_error(data, "capability 'verify_email' is kind 'tool' but route 'claude' is a 'ai' provider")


def test_route_lists_must_be_unique() -> None:
    data = fixture_data()
    data["capabilities"]["verify_email"]["routes"] = ["reoon", "reoon"]
    expect_error(data, "more than once: reoon")


@pytest.mark.parametrize(
    ("locate", "key", "location"),
    [
        (lambda d: d["capabilities"]["verify_email"], "strategy", "capabilities.verify_email.strategy"),
        (lambda d: d["providers"]["reoon"], "default_strategy", "providers.reoon.default_strategy"),
        (
            lambda d: d["providers"]["reoon"]["connections"][0],
            "strategy",
            "providers.reoon.connections.0.strategy",
        ),
    ],
    ids=["capability", "provider", "connection"],
)
def test_strategy_names_are_checked_everywhere(locate: Any, key: str, location: str) -> None:
    data = fixture_data()
    locate(data)[key] = "cheapest_first"
    message = expect_error(data, location)
    assert "cheapest_first" not in message  # rejected input is never echoed
    for name in STRATEGIES:
        assert f"'{name}'" in message  # but the allowed names are listed


def test_every_allowed_strategy_is_accepted() -> None:
    assert set(STRATEGIES) == {
        "failover",
        "most_remaining",
        "round_robin",
        "parallel_split",
        "sticky",
        "fit_check",
        "pin",
    }
    for name in STRATEGIES:
        data = fixture_data()
        data["capabilities"]["verify_email"]["strategy"] = name
        data["providers"]["reoon"]["connections"][0]["strategy"] = name
        assert load_data(data).capabilities["verify_email"].strategy == name


def test_unknown_keys_are_rejected_so_typos_are_not_silent() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["units"]["credits"]["chaged_on"] = "success"
    expect_error(data, "providers.reoon.connections.0.units.credits.chaged_on")
    data = fixture_data()
    data["budgets"]["per_provder"] = {"clay": 1}
    expect_error(data, "budgets.per_provder")


def test_enum_fields_are_checked() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["status"] = "sleeping"
    data["providers"]["reoon"]["executor"] = "carrier_pigeon"
    data["providers"]["reoon"]["connections"][0]["units"]["credits"]["period"] = "fortnight"
    expect_error(
        data,
        "providers.reoon.connections.0.status",
        "providers.reoon.executor",
        "providers.reoon.connections.0.units.credits.period",
    )


def test_anchor_only_applies_to_monthly_periods_and_must_be_a_day() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["units"]["credits"]["anchor"] = 5  # period is 'day'
    expect_error(data, "anchor (day of month) only applies to period 'month'")
    data = fixture_data()
    data["providers"]["zerobounce"]["connections"][0]["units"]["credits"]["anchor"] = 32
    expect_error(data, "providers.zerobounce.connections.0.units.credits.anchor")


def test_negative_numbers_are_rejected() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["units"]["credits"]["limit"] = -1
    data["budgets"]["global_monthly_usd"] = -5
    data["providers"]["reoon"]["connections"][0]["concurrency"] = 0
    expect_error(
        data,
        "providers.reoon.connections.0.units.credits.limit",
        "budgets.global_monthly_usd",
        "providers.reoon.connections.0.concurrency",
    )


def test_ids_must_be_slugs() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["id"] = "Reoon 01!"
    expect_error(data, "providers.reoon.connections.0.id")


def test_budget_refs_must_exist() -> None:
    data = fixture_data()
    data["budgets"]["per_provider"] = {"nope": 1}
    data["budgets"]["per_connection"] = {"nope-01": 1}
    expect_error(data, "budgets.per_provider: 'nope' is not a provider", "budgets.per_connection: 'nope-01'")


def test_settings_and_budget_global_must_agree_when_both_given() -> None:
    data = fixture_data()
    data["settings"]["global_monthly_budget_usd"] = 10
    expect_error(data, "disagree")
    data["settings"]["global_monthly_budget_usd"] = 50
    assert load_data(data).budgets.global_monthly_usd == 50


def test_settings_validation() -> None:
    data = fixture_data()
    data["settings"]["owner_email"] = "not-an-email"
    data["settings"]["timezone"] = "Mars/Olympus"
    expect_error(data, "settings.owner_email", "settings.timezone")
    data = fixture_data()
    data["settings"]["alert_thresholds"] = [100, 50, 50, 80]
    assert load_data(data).settings.alert_thresholds == [50, 80, 100]


def test_all_problems_are_reported_together_not_one_at_a_time() -> None:
    data = fixture_data()
    data["capabilities"]["verify_email"]["routes"] = ["ghost"]
    data["providers"]["reoon"]["connections"][1]["id"] = "reoon-01"
    data["budgets"]["per_provider"] = {"phantom": 1}
    message = expect_error(data, "ghost", "reoon-01", "phantom")
    assert message.startswith(
        "invalid registry test.yaml: 1 error(s)"
    )  # one cross-reference error listing all three


def test_a_pasted_secret_in_auth_ref_is_rejected_and_never_echoed() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["auth_ref"] = "PASTED-NOT-REAL-KEY-0123456789abcdef"
    message = expect_error(data, "providers.reoon.connections.0.auth_ref")
    assert "PASTED-NOT-REAL-KEY" not in message


def test_a_pasted_secret_in_any_other_field_is_not_echoed_either() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["status"] = "PASTED-NOT-REAL-KEY-0123456789abcdef"
    message = expect_error(data, "providers.reoon.connections.0.status")
    assert "PASTED-NOT-REAL-KEY" not in message


def test_registry_validation_error_outside_the_loader_also_hides_input() -> None:
    data = fixture_data()
    data["providers"]["reoon"]["connections"][0]["auth_ref"] = "PASTED-NOT-REAL-KEY-0123456789abcdef"
    with pytest.raises(ValidationError) as caught:
        Registry.model_validate(data)
    assert "PASTED-NOT-REAL-KEY" not in str(caught.value)


# --- YAML / file level -------------------------------------------------------------------------------


def test_duplicate_yaml_keys_are_an_error() -> None:
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "  zerobounce:\n", "  reoon:\n    kind: tool\n    executor: api\n  zerobounce:\n", 1
    )
    with pytest.raises(RegistryError, match=r"duplicate key 'reoon'"):
        parse_registry(text, source="dup.yaml")


def test_yaml_syntax_error_reports_the_position() -> None:
    with pytest.raises(RegistryError, match=r"cannot parse registry bad\.yaml \(line \d+, column \d+\): ."):
        parse_registry("settings:\n  owner_email: [unclosed\nproviders: {}\n", source="bad.yaml")


def test_top_level_must_be_a_mapping() -> None:
    with pytest.raises(RegistryError, match="top level must be a mapping"):
        parse_registry("- just\n- a list\n", source="list.yaml")


def test_missing_required_sections_are_named() -> None:
    """Blank start: providers and capabilities are optional (an empty Farm is valid); settings still are not."""
    with pytest.raises(RegistryError) as caught:
        parse_registry("budgets: {}\n", source="thin.yaml")
    message = str(caught.value)
    assert "settings" in message
    assert "providers" not in message and "capabilities" not in message

    blank = parse_registry("settings: {owner_email: owner@example.com}\n", source="blank.yaml")
    assert blank.providers == {} and blank.capabilities == {}


def test_missing_file_is_a_registry_error(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="cannot read registry file"):
        load_registry(tmp_path / "nope.yaml")


# --- JSON Schema -------------------------------------------------------------------------------------


def test_export_json_schema_is_valid_json_schema_with_the_expected_definitions() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = export_json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    json.dumps(schema)  # serialisable as is
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert {"ProviderSpec", "ConnectionSpec", "UnitSpec", "CapabilitySpec"} <= set(schema["$defs"])
    provider = schema["$defs"]["ProviderSpec"]["properties"]
    assert {"name", "kind", "executor", "default_strategy", "enabled", "config", "connections"} <= set(
        provider
    )
    connection = schema["$defs"]["ConnectionSpec"]["properties"]
    assert {
        "id",
        "label",
        "auth_ref",
        "scope",
        "priority",
        "strategy",
        "concurrency",
        "rate_per_min",
        "status",
        "plan",
        "meta",
        "units",
    } <= set(connection)
    assert set(schema["$defs"]["UnitSpec"]["properties"]) == {
        "limit",
        "period",
        "anchor",
        "charged_on",
        "unit_cost_usd",
        "estimate_per_call",
    }
    assert schema["$defs"]["ConnectionSpec"]["additionalProperties"] is False


def test_schema_offers_forms_the_right_choices() -> None:
    schema = export_json_schema()
    unit = schema["$defs"]["UnitSpec"]["properties"]
    assert set(schema["$defs"]["ProviderSpec"]["properties"]["kind"]["enum"]) == {"tool", "ai"}
    assert unit["charged_on"]["enum"] == ["attempt", "success", "found"]
    assert "rolling_5h" in unit["period"]["enum"]
    strategy = schema["$defs"]["ProviderSpec"]["properties"]["default_strategy"]["enum"]
    assert strategy == list(STRATEGIES)
    for name, default in (("unit_cost_usd", 0), ("estimate_per_call", 1)):
        # a plain number input with a numeric default, not Decimal's anyOf / string default
        assert (unit[name]["type"], unit[name]["minimum"], unit[name]["default"]) == ("number", 0, default)


@pytest.mark.parametrize("path", [REAL, EXAMPLE, FIXTURE], ids=["real", "example", "fixture"])
def test_registries_dumped_as_json_validate_against_the_exported_schema(path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    document = load_registry(path).model_dump(mode="json")
    jsonschema.Draft202012Validator(export_json_schema()).validate(document)


def test_schema_rejects_what_the_models_reject() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(export_json_schema())
    document = load_registry(FIXTURE).model_dump(mode="json")
    document["providers"]["reoon"]["connections"][0]["status"] = "sleeping"
    document["providers"]["reoon"]["connections"][0]["surprise"] = 1
    messages = " ".join(e.message for e in validator.iter_errors(document))
    assert "sleeping" in messages and "surprise" in messages
