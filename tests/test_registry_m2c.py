"""The shipped registry (config/registry.yaml) agrees with the adapters and models written for it (M2c).

A registry that routes a capability to a pool whose adapter cannot serve it, or that lacks a consumption
unit an adapter reports, only fails at run time; these tests make it fail at build time instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from farm.adapters import ADAPTERS
from farm.capabilities.schemas import CAPABILITY_MODELS
from farm.executors.llm import LlmExecutor
from farm.registry.loader import load_registry
from farm.registry.models import Registry

REAL = Path(__file__).resolve().parent.parent / "config" / "registry.example.yaml"

# What each adapter's ``units_used`` can contain (the keys of the dicts it returns).
EMITTED_UNITS = {
    "reoon": {"credits"},
    "zerobounce": {"credits"},
    "apollo": {"credits"},
    "hunter": {"searches", "verifications"},
    "pagespeed": {"requests"},
    "adzuna": {"requests"},
    "ats_public": {"requests"},
    "llm": {"requests", "tokens"},
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry(REAL)


def served_by(provider_id: str, executor: str) -> set[str] | None:
    """Capabilities the code behind a pool can serve; ``None`` for pools implemented elsewhere (MCP, CLIs)."""
    if executor == "api":
        return set(ADAPTERS[provider_id]().capabilities())
    if executor == "llm":
        return set(LlmExecutor().capabilities())
    return None


def test_every_api_pool_has_an_adapter_and_every_adapter_a_pool(registry: Registry) -> None:
    api_pools = {pid for pid, p in registry.providers.items() if p.executor == "api"}
    assert api_pools == set(ADAPTERS)


def test_every_route_leads_to_a_pool_that_can_serve_the_capability(registry: Registry) -> None:
    checked = 0
    for name, capability in registry.capabilities.items():
        for route in capability.routes:
            served = served_by(route, registry.providers[route].executor)
            if served is not None:
                assert name in served, (
                    f"capability '{name}' routes to '{route}', whose code does not serve it"
                )
                checked += 1
    assert (
        checked == 12
    )  # verify x3, find_email x2, find_person, enrich, pagespeed, jobs x2, extract, classify


def test_every_capability_a_pool_serves_is_routed_to_it(registry: Registry) -> None:
    """The reverse: a capability an adapter implements but no route mentions would be dead code."""
    for pid, provider in registry.providers.items():
        served = served_by(pid, provider.executor)
        if served is None:
            continue
        routed = {name for name, c in registry.capabilities.items() if pid in c.routes}
        assert served == routed, f"pool '{pid}' serves {sorted(served)} but is routed for {sorted(routed)}"


def test_every_tool_capability_has_input_and_output_models(registry: Registry) -> None:
    tool_capabilities = {name for name, c in registry.capabilities.items() if c.kind == "tool"}
    assert tool_capabilities <= set(CAPABILITY_MODELS)
    assert tool_capabilities == {
        "verify_email",
        "find_person",
        "find_email",
        "enrich_company",
        "pagespeed",
        "jobs_lookup",
        "extract",
        "classify",
    }


def test_the_models_json_schemas_export(registry: Registry) -> None:
    """The Console and the MCP gateway generate forms and tool signatures from these schemas."""
    for name, (model_in, model_out) in CAPABILITY_MODELS.items():
        assert model_in.model_json_schema()["type"] == "object", name
        assert model_out.model_json_schema()["type"] == "object", name


@pytest.mark.parametrize("pool", sorted(EMITTED_UNITS))
def test_every_connection_declares_each_unit_its_adapter_reports(registry: Registry, pool: str) -> None:
    for connection in registry.providers[pool].connections:
        assert set(connection.units) == EMITTED_UNITS[pool], connection.id


def test_charge_modes_follow_the_providers_billing_rules(registry: Registry) -> None:
    apollo = registry.providers["apollo"].connections[0].units["credits"]
    assert apollo.charged_on == "success"  # a match is billed even when no email comes back
    hunter = registry.providers["hunter"].connections[0].units
    assert (hunter["searches"].charged_on, hunter["verifications"].charged_on) == ("found", "success")
    pagespeed = registry.providers["pagespeed"].connections[0].units["requests"]
    assert (pagespeed.limit, pagespeed.period) == (25000, "day")
    adzuna = registry.providers["adzuna"].connections[0]
    assert (adzuna.units["requests"].limit, adzuna.units["requests"].period, adzuna.rate_per_min) == (
        250,
        "day",
        25,
    )
    assert adzuna.meta == {"app_id_ref": "env:ADZUNA_APP_ID"}  # a reference, never a value


def test_the_llm_pool_matches_the_brief(registry: Registry) -> None:
    pool = registry.providers["llm"]
    assert (pool.kind, pool.executor) == ("tool", "llm")
    by_id = {c.id: c for c in pool.connections}
    assert list(by_id) == ["llm-or-free", "llm-or-flash", "llm-groq", "llm-bedrock"]
    assert by_id["llm-or-free"].meta["model"] == "openrouter/qwen/qwen3.8-27b:free"
    assert by_id["llm-or-flash"].meta["model"] == "openrouter/deepseek/deepseek-v4-flash"
    assert by_id["llm-groq"].status == "needs_login"  # until a Groq key exists
    assert by_id["llm-bedrock"].status == "paused"  # the owner enables it
    assert by_id["llm-or-free"].status == by_id["llm-or-flash"].status == "active"
    # one Bifrost virtual key for all of them: the Farm never holds a provider key
    assert {c.auth_ref for c in pool.connections} == {"env:BIFROST_FARM_VK"}
    assert all(c.meta["model"] for c in pool.connections)
    assert by_id["llm-or-free"].rate_per_min == 20  # OpenRouter free models: 20 requests/minute


def test_routes_match_the_brief(registry: Registry) -> None:
    routes = {name: c.routes for name, c in registry.capabilities.items()}
    assert routes["find_person"] == ["apollo", "clay"]
    assert routes["find_email"] == ["hunter", "apollo"]
    assert routes["enrich_company"] == ["apollo", "clay"]
    assert routes["pagespeed"] == ["pagespeed"]
    assert routes["jobs_lookup"] == ["ats_public", "adzuna"]
    assert routes["extract"] == routes["classify"] == ["llm"]
    assert routes["verify_email"] == ["reoon", "zerobounce", "hunter"]  # Hunter as the third verify pool
    for name in (
        "extract",
        "classify",
        "find_person",
        "find_email",
        "enrich_company",
        "pagespeed",
        "jobs_lookup",
    ):
        assert registry.capabilities[name].strategy == "failover"


def test_the_public_board_connection_needs_no_secret(registry: Registry) -> None:
    connection = registry.providers["ats_public"].connections[0]
    assert connection.auth_ref == "cli:none" and connection.status == "active"
    assert ADAPTERS["ats_public"].requires_auth is False


def test_existing_entries_are_kept(registry: Registry) -> None:
    assert {"reoon", "zerobounce", "apollo", "hunter", "clay", "claude", "gemini", "codex", "hermes"} <= set(
        registry.providers
    )
    assert registry.providers["apollo"].connections[0].status == "needs_login"
    assert registry.providers["reoon"].connections[0].units["credits"].limit == 20
    assert registry.capabilities["ask_ai"].routes == ["claude", "gemini", "codex", "hermes"]
    assert set(registry.budgets.per_provider) == set(registry.providers)
