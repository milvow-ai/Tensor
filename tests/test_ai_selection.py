"""The explicit model/effort choice: the pure policy, and what ``JobManager``, ``ask_ai`` and the CLI gate do with it.

Refusals must leave nothing behind: no ``ai_jobs`` row, no quota reservation, no CLI process.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from farm.ai import selection
from farm.ai.failures import AiRequestError
from farm.ai.jobs import offers_choice
from farm.capabilities.schemas import AiJobSpec
from farm.control.commands import AddConnectionPayload, AddProviderPayload
from farm.db.pool import tenant_connection
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.executors.cli_agent import CliAgentExecutor
from tests.farm_helpers import fetch
from tests.test_ai_jobs import (  # noqa: F401  (the fixtures are used by name)
    Aip,
    acct,
    agy_ok,
    aip,
    aip_factory,
    call,
    claude_ok,
    finish,
    start,
)

# Imported pytest fixtures are re-exported intentionally for this test module.
__all__ = ["aip", "aip_factory"]

CLAUDE_META: dict[str, Any] = {"models": ["sonnet", "opus"]}
DEFAULTS: dict[str, Any] = {"enabled": True, "model": "opus", "effort": "high"}
LEGACY: dict[str, Any] = {"model": "sonnet", "default_model": "opus", "effort": "low", "default_effort": "low"}


# --- the policy ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "model", "effort", "missing"),
    [
        ("claude", None, None, ("model", "effort")),
        ("claude", "opus", None, ("effort",)),
        ("codex", None, "high", ("model",)),
        ("gemini", None, None, ("model",)),
        ("gemini", "flash", None, ()),
        ("any", "opus", None, ()),  # the account decides; resolved once one is planned
    ],
)
def test_what_a_choice_lacks_depends_on_the_provider(
    provider: str, model: str | None, effort: str | None, missing: tuple[str, ...]
) -> None:
    assert selection.missing_fields(provider, model=model, effort=effort) == missing


def test_an_explicit_choice_resolves_in_the_configured_spelling() -> None:
    choice = selection.resolve("claude", CLAUDE_META, model="OPUS", effort="max")
    assert choice == selection.Choice("opus", "max", "max", "explicit")


def test_codex_requests_stay_requested_and_the_applied_effort_is_reported() -> None:
    choice = selection.resolve("codex", {"models": ["gpt-5"]}, model="gpt-5", effort="xhigh")
    assert isinstance(choice, selection.Choice)
    assert (choice.effort, choice.effective_effort) == ("xhigh", "high")


@pytest.mark.parametrize(
    ("provider", "meta", "model", "effort"),
    [
        ("claude", CLAUDE_META, "gpt-5", "high"),
        ("claude", {**CLAUDE_META, "efforts": ["low"]}, "opus", "high"),
        ("gemini", {"models": ["flash"]}, "flash", "high"),
    ],
)
def test_a_value_the_account_does_not_offer_is_invalid(
    provider: str, meta: dict[str, Any], model: str, effort: str
) -> None:
    problem = selection.resolve(provider, meta, model=model, effort=effort)
    assert isinstance(problem, selection.Problem) and problem.kind == "invalid_selection"


def test_defaults_apply_only_with_use_defaults_and_an_enabled_valid_opt_in() -> None:
    meta = {**CLAUDE_META, "selection_defaults": DEFAULTS}
    assert selection.resolve("claude", meta, model=None, effort=None, use_defaults=True) == selection.Choice(
        "opus", "high", "high", "account_defaults"
    )
    without = selection.resolve("claude", meta, model=None, effort=None)
    assert isinstance(without, selection.Problem) and without.kind == "selection_required"
    disabled = {**CLAUDE_META, "selection_defaults": {**DEFAULTS, "enabled": False}}
    refused = selection.resolve("claude", disabled, model=None, effort=None, use_defaults=True)
    assert isinstance(refused, selection.Problem) and refused.kind == "selection_required"


def test_an_explicit_value_wins_over_the_default_it_shares_a_request_with() -> None:
    meta = {**CLAUDE_META, "selection_defaults": DEFAULTS}
    choice = selection.resolve("claude", meta, model="sonnet", effort=None, use_defaults=True)
    assert choice == selection.Choice("sonnet", "high", "high", "account_defaults")


@pytest.mark.parametrize(
    "broken",
    [
        {"enabled": True, "model": "gpt-5", "effort": "high"},
        {"enabled": True, "model": "opus", "effort": "turbo"},
        {"enabled": True, "model": "opus"},
        {"enabled": True, "effort": "high"},
        {"enabled": "true", "model": "opus", "effort": "high"},
    ],
)
def test_broken_defaults_are_not_defaults(broken: dict[str, Any]) -> None:
    meta = {**CLAUDE_META, "selection_defaults": broken}
    refused = selection.resolve("claude", meta, model=None, effort=None, use_defaults=True)
    assert isinstance(refused, selection.Problem) and refused.kind == "selection_required"


def test_legacy_meta_keys_are_never_an_opt_in() -> None:
    assert selection.defaults_of("claude", LEGACY).enabled is False
    refused = selection.resolve("claude", LEGACY, model=None, effort=None, use_defaults=True)
    assert isinstance(refused, selection.Problem) and refused.kind == "selection_required"


def test_the_menu_separates_configured_from_verified_and_says_when_effort_is_not_supported() -> None:
    menu = selection.account_menu("codex", {"models": ["gpt-5", "o9"], "verified_models": ["GPT-5"]})
    assert menu["models"] == [{"id": "gpt-5", "source": "verified"}, {"id": "o9", "source": "configured"}]
    assert menu["efforts"]["applied_as"] == {"xhigh": "high", "max": "high"}
    assert menu["defaults"]["enabled"] is False
    other = selection.account_menu("gemini", {"models": ["flash"]})
    assert other["efforts"] == {"supported": False, "values": [], "note": "not_supported"}


def test_failover_needs_both_the_model_and_the_effort() -> None:
    narrow = acct("claude-03", meta={"models": ["sonnet"], "efforts": ["low"]})
    assert offers_choice(narrow, "sonnet", "low")
    assert not offers_choice(narrow, "sonnet", "high")
    assert not offers_choice(narrow, "opus", "low")


# --- saving defaults ----------------------------------------------------------------------------------------


def test_add_provider_accepts_disabled_or_valid_defaults_and_rejects_the_rest() -> None:
    base: dict[str, Any] = {"provider_id": "claude", "kind": "ai", "cli": "claude", "models": ["sonnet", "opus"]}
    AddProviderPayload(**base)
    AddProviderPayload(**base, meta={"selection_defaults": {"enabled": False}})
    AddProviderPayload(**base, meta={"selection_defaults": DEFAULTS})
    for broken in (
        {"enabled": True, "model": "gpt-5", "effort": "high"},
        {"enabled": True, "model": "opus"},
        {"enabled": True, "model": "opus", "effort": "high", "extra": 1},
        {"model": "opus"},
    ):
        with pytest.raises(ValidationError, match="selection_defaults"):
            AddProviderPayload(**base, meta={"selection_defaults": broken})


def test_add_connection_checks_defaults_against_the_meta_it_saves() -> None:
    base: dict[str, Any] = {"provider_id": "gemini", "id": "agy-09", "auth_ref": "cli:agy-09"}
    AddConnectionPayload(
        **base, meta={"models": ["flash"], "selection_defaults": {"enabled": True, "model": "flash"}}
    )
    with pytest.raises(ValidationError, match="selection_defaults"):
        AddConnectionPayload(
            **base,
            meta={
                "models": ["flash"],
                "selection_defaults": {"enabled": True, "model": "flash", "effort": "high"},
            },
        )


# --- JobManager: nothing exists until the choice does -------------------------------------------------------


async def set_meta(aip: Aip, account: str, meta: dict[str, Any]) -> None:
    async with tenant_connection(aip.pool) as conn:
        await conn.execute(
            "update public.connections set meta = meta || %s::jsonb where id = %s", (json.dumps(meta), account)
        )


async def job_row(aip: Aip, job_id: Any) -> tuple[Any, ...]:
    rows = await fetch(
        aip.pool,
        "select model, effort, effective_effort, selection_source from public.ai_jobs where id = %s",
        job_id,
    )
    return rows[0]


async def assert_nothing_started(aip: Aip) -> None:
    assert await fetch(aip.pool, "select id from public.ai_jobs") == []
    assert await fetch(aip.pool, "select id from public.quota_reservations") == []
    assert aip.harness.events() == []


async def refusal(aip: Aip, *specs: AiJobSpec, **options: Any) -> AiRequestError:
    with pytest.raises(AiRequestError) as caught:
        await aip.manager.start_many(list(specs), caller="test", distinct_accounts=False, **options)
    return caught.value


def raw(**fields: Any) -> AiJobSpec:
    """A spec exactly as given (``tests.test_ai_jobs.spec`` fills in a valid choice)."""
    return AiJobSpec(task="do the work", **fields)


@pytest.mark.parametrize(
    ("fields", "missing"),
    [
        ({"ai": "claude"}, ["model", "effort"]),
        ({"ai": "claude", "model": "opus"}, ["effort"]),
        ({"account": "claude-03", "effort": "high"}, ["model"]),
        ({"ai": "gemini"}, ["model"]),
    ],
)
async def test_a_job_without_a_choice_is_refused_with_the_menu_and_leaves_nothing(
    aip: Aip, fields: dict[str, Any], missing: list[str]
) -> None:
    error = await refusal(aip, raw(**fields))
    assert error.kind == "selection_required"
    assert error.choices is not None and error.choices[0]["missing"] == missing
    assert error.choices[0]["options"], "the refusal carries what can be chosen"
    await assert_nothing_started(aip)


async def test_one_job_without_a_choice_refuses_the_whole_batch(aip: Aip) -> None:
    error = await refusal(aip, raw(ai="claude", model="opus", effort="high"), raw(ai="claude"))
    assert error.kind == "selection_required"
    assert error.choices is not None and [c["index"] for c in error.choices] == [1]
    await assert_nothing_started(aip)


@pytest.mark.parametrize(
    "fields",
    [
        {"ai": "claude", "model": "gpt-5", "effort": "high"},
        {"account": "claude-02", "model": "gpt-5", "effort": "high"},
        {"ai": "gemini", "model": "gemini-3.8-flash-high", "effort": "high"},
    ],
)
async def test_an_invalid_choice_is_refused_with_the_menu_and_leaves_nothing(
    aip: Aip, fields: dict[str, Any]
) -> None:
    error = await refusal(aip, raw(**fields))
    assert error.kind == "invalid_selection" and error.choices
    await assert_nothing_started(aip)


async def test_an_effort_the_account_narrowed_away_is_invalid(aip: Aip) -> None:
    await set_meta(aip, "claude-02", {"efforts": ["low"]})
    error = await refusal(aip, raw(account="claude-02", model="opus", effort="high"))
    assert error.kind == "invalid_selection"
    await assert_nothing_started(aip)


async def test_an_explicit_choice_is_persisted_and_reported_by_status(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("done"))
    async with aip.client() as client:
        started = await start(client, ai="claude", model="opus", effort="max")
        assert (started["model"], started["effort"], started["selection_source"]) == ("opus", "max", "explicit")
        assert await job_row(aip, started["job_id"]) == ("opus", "max", "max", "explicit")
        (status,) = (await call(client, "ai_status", job_ids=[started["job_id"]]))["jobs"]
        assert (status["model"], status["effort"], status["effective_effort"]) == ("opus", "max", "max")
        await finish(client, [started["job_id"]])
    (event,) = aip.harness.events()
    assert "opus" in event["argv"]


async def test_codex_persists_what_was_asked_and_what_is_applied(
    aip_factory: Callable[..., Awaitable[Aip]],
) -> None:
    farm = await aip_factory(codex=1)
    (started,) = await farm.manager.start_many(
        [raw(ai="codex", model="gpt-5", effort="xhigh")], caller="test", distinct_accounts=False
    )
    assert await job_row(farm, started.job_id) == ("gpt-5", "xhigh", "high", "explicit")
    assert started.effective_effort == "high"


async def test_opted_in_defaults_run_and_are_recorded_as_defaults(aip: Aip) -> None:
    await set_meta(aip, "claude-02", {"selection_defaults": DEFAULTS})
    aip.harness.set_response(stdout=claude_ok("done"))
    (started,) = await aip.manager.start_many(
        [raw(account="claude-02", use_defaults=True)], caller="test", distinct_accounts=False
    )
    assert await job_row(aip, started.job_id) == ("opus", "high", "high", "account_defaults")


async def test_use_defaults_without_an_opt_in_is_refused(aip: Aip) -> None:
    error = await refusal(aip, raw(account="claude-02", use_defaults=True))
    assert error.kind == "selection_required"
    await assert_nothing_started(aip)


async def test_legacy_meta_keys_are_ignored_with_and_without_use_defaults(aip: Aip) -> None:
    await set_meta(aip, "claude-02", LEGACY)
    for fields in ({"account": "claude-02"}, {"account": "claude-02", "use_defaults": True}):
        error = await refusal(aip, raw(**fields))
        assert error.kind == "selection_required"
    await assert_nothing_started(aip)


# --- continuations -------------------------------------------------------------------------------------------


async def test_a_reply_inherits_the_choice_and_may_only_change_the_effort(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("done", session="s-1"))
    async with aip.client() as client:
        first = await start(client, ai="claude", model="opus", effort="high")
        await finish(client, [first["job_id"]])
        conversation = first["conversation_id"]

        same = await call(client, "ai_reply", conversation_id=conversation, message="more")
        assert same["ok"] is True, same
        assert await job_row(aip, same["job_id"]) == ("opus", "high", "high", "inherited")
        await finish(client, [same["job_id"]])

        lower = await call(client, "ai_reply", conversation_id=conversation, message="more", effort="low")
        assert await job_row(aip, lower["job_id"]) == ("opus", "low", "low", "explicit")
        await finish(client, [lower["job_id"]])

        other = await call(client, "ai_reply", conversation_id=conversation, message="more", model="sonnet")
        assert other["ok"] is False and other["error"]["kind"] == "bad_request"


async def test_a_reply_cannot_pick_an_effort_the_account_does_not_offer(aip: Aip) -> None:
    aip.harness.set_response(stdout=claude_ok("done", session="s-1"))
    async with aip.client() as client:
        first = await start(client, account="claude-02", model="opus", effort="low")
        await finish(client, [first["job_id"]])
        await set_meta(aip, "claude-02", {"efforts": ["low"]})
        refused = await call(
            client, "ai_reply", conversation_id=first["conversation_id"], message="more", effort="high"
        )
    assert refused["ok"] is False and refused["error"]["kind"] == "invalid_selection"
    assert refused["choices"][0]["kind"] == "invalid_selection"


# --- the blocking path and the menu tool ---------------------------------------------------------------------


async def test_ask_ai_without_a_choice_answers_with_the_menu_before_the_router(aip: Aip) -> None:
    async with aip.client() as client:
        reply = await call(client, "ask_ai", ai="claude", task="do the work")
    assert reply["ok"] is False and reply["error"]["kind"] == "selection_required"
    assert reply["choices"][0]["missing"] == ["model", "effort"]
    assert await fetch(aip.pool, "select id from public.quota_reservations") == []
    assert await fetch(aip.pool, "select id from public.runs") == []
    assert aip.harness.events() == []


async def test_ai_options_lists_accounts_with_their_choices(aip: Aip) -> None:
    await set_meta(aip, "claude-02", {"verified_models": ["sonnet"]})
    async with tenant_connection(aip.pool) as conn:
        await conn.execute("update connections set label='Claude work login' where id='claude-02'")
    async with aip.client() as client:
        reply = await call(client, "ai_options", ai="claude")
    (provider,) = reply["providers"]
    assert provider["effort_supported"] is True
    first = next(a for a in provider["accounts"] if a["account"] == "claude-02")
    assert first["label"] == "Claude work login"
    assert first["models"] == [{"id": "sonnet", "source": "verified"}, {"id": "opus", "source": "configured"}]


# --- a caller that goes straight to the executor -------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.calls: list[ExecRequest] = []

    async def execute(self, req: ExecRequest) -> ExecResult:
        self.calls.append(req)
        return ExecResult(ok=True, data={"text": "ok"})


def direct(params: dict[str, Any], *, provider: str = "claude", **options: Any) -> ExecRequest:
    return ExecRequest(
        request_id=uuid4(),
        capability="ask_ai",
        params={"task": "do the work", **params},
        connection=ConnectionView(
            id=f"{provider}-02", provider_id=provider, auth_ref="cli:x", meta={"models": ["opus"], **LEGACY}
        ),
        **options,
    )


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"model": "opus"},
        {"effort": "high"},
        {"model": "gpt-5", "effort": "high"},
        {"model": "opus", "effort": "turbo"},
    ],
)
async def test_the_executor_refuses_without_a_valid_choice_and_starts_no_cli(params: dict[str, Any]) -> None:
    driver = Recorder()
    result = await CliAgentExecutor(drivers={"claude": driver}).execute(direct(params))
    assert result.ok is False and result.error_kind == ErrorKind.BAD_REQUEST
    assert driver.calls == []


async def test_the_executor_passes_a_valid_choice_and_the_operators_ping() -> None:
    driver = Recorder()
    executor = CliAgentExecutor(drivers={"claude": driver})
    assert (await executor.execute(direct({"model": "opus", "effort": "high"}))).ok
    assert (await executor.execute(direct({}, selection_exempt=True))).ok
    assert len(driver.calls) == 2
