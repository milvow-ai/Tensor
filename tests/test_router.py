"""Router behaviour: the rules (pure) and the flow (real Postgres, scripted executors).

The executors here are scripted so each test can force exactly the failure it guards: a provider that raises,
hangs, answers empty, answers with garbage, or fails after spending units. HTTP-level behaviour is covered by the
acceptance tests (respx + the real adapters).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.registry import CapabilitySpec, Registry
from farm.resources import health as health_rules
from farm.resources import ledger, router
from farm.resources.router import (
    Budget,
    Candidate,
    UnitConfig,
    UnknownCapability,
    charge_for,
    paid_spend_block,
    request_hash,
    route,
    skip_reason,
)
from farm.secrets import register_secret
from tests.conftest import START, FakeClock
from tests.farm_helpers import (
    EMAIL,
    AsyncFnExecutor,
    ScriptedExecutor,
    events,
    failure,
    fetch,
    health,
    kinds,
    ok_result,
    only_reoon_01,
    quota,
    reservations,
    run_row,
    status_of,
)

type Make = Callable[..., Awaitable[FarmContext]]


# --- pure rules -------------------------------------------------------------------------------------------


def unit(
    charged_on: str = "success", *, cost: str = "0", estimate: str = "1", name: str = "credits"
) -> UnitConfig:
    return UnitConfig(name, Decimal(10), "day", charged_on, Decimal(cost), Decimal(estimate))


def cand(
    id: str = "c1",
    *,
    status: str = "active",
    circuit: str = "closed",
    cooldown_until: Any = None,
    scope: tuple[str, ...] = ("internal",),
    units: tuple[UnitConfig, ...] = (),
    provider: str = "p",
) -> Candidate:
    return Candidate(id, provider, "env:X", scope, 1, status, {}, 1, None, circuit, cooldown_until, units)


def test_request_hash_is_the_documented_sha256_and_ignores_key_order() -> None:
    expected = hashlib.sha256(b'verify_email|{"a":1,"b":[2,3]}').hexdigest()
    assert (
        request_hash("verify_email", {"b": [2, 3], "a": 1})
        == expected
        == request_hash("verify_email", {"a": 1, "b": [2, 3]})
    )
    assert request_hash("verify_email", {"a": 1}) != request_hash("find_email", {"a": 1})


@pytest.mark.parametrize(
    ("charged_on", "result", "expected"),
    [
        # the executor's report is the truth, whatever the outcome
        ("success", ok_result(units={"credits": 3}), "3"),
        ("success", failure(ErrorKind.SERVER).model_copy(update={"units_used": {"credits": 2}}), "2"),
        ("attempt", ok_result(units={"credits": 4}), "4"),
        ("found", ok_result(units={"credits": 1}, found=False), "1"),
        # nothing reported: charged_on decides
        ("attempt", failure(ErrorKind.SERVER), "1"),  # the estimate: an attempt is the charge
        ("attempt", ok_result(units={}), "1"),
        ("success", failure(ErrorKind.SERVER), "0"),
        ("success", ok_result(units={}, found=False), "0"),  # a refunded inconclusive verdict
        ("found", ok_result(units={}, found=False), "0"),
    ],
)
def test_charge_for(charged_on: str, result: ExecResult, expected: str) -> None:
    amount, reason = charge_for(unit(charged_on), result)
    assert amount == Decimal(expected) and reason


def test_charge_for_only_looks_at_its_own_unit() -> None:
    result = ok_result(units={"tokens": 900})
    assert charge_for(unit("success", name="requests"), result)[0] == 0
    assert charge_for(unit("success", name="tokens"), result)[0] == 900


@pytest.mark.parametrize(
    ("budgets", "expected"),
    [
        ({}, None),  # no budget at all: nothing blocks
        ({("global", None): Budget(Decimal(50), True)}, None),
        ({("global", None): Budget(Decimal(0), True)}, "global budget is 0"),
        ({("provider", "p"): Budget(Decimal(0), True)}, "provider budget (p) is 0"),
        ({("connection", "c1"): Budget(Decimal(0), True)}, "connection budget (c1) is 0"),
        ({("provider", "p"): Budget(Decimal(0), False)}, None),  # a soft cap does not hard-stop
        ({("provider", "other"): Budget(Decimal(0), True)}, None),
        (
            {("global", None): Budget(Decimal(0), True), ("provider", "p"): Budget(Decimal(9), True)},
            "global budget is 0",
        ),
    ],
)
def test_paid_connections_are_blocked_by_a_zero_budget(
    budgets: dict[Any, Budget], expected: str | None
) -> None:
    paid = cand(units=(unit(cost="0.074"),))
    blocked = paid_spend_block(paid, budgets)
    assert (blocked is None) == (expected is None)
    if expected is not None:
        assert blocked is not None and blocked.startswith(expected)


def test_free_connections_are_never_blocked_by_a_zero_budget() -> None:
    zero = {("global", None): Budget(Decimal(0), True), ("provider", "p"): Budget(Decimal(0), True)}
    assert paid_spend_block(cand(units=(unit(cost="0"),)), zero) is None
    assert paid_spend_block(cand(units=()), zero) is None
    assert (
        paid_spend_block(cand(units=(unit(cost="1", estimate="0"),)), zero) is None
    )  # nothing is spent per call


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (cand(), None),
        (cand(status="paused"), "paused"),
        (cand(status="disabled"), "disabled"),
        (cand(status="needs_login"), "needs_login"),
        (cand(status="exhausted"), "exhausted"),
        (cand(status="exhausted", cooldown_until=START + timedelta(seconds=5)), "exhausted"),
        (
            cand(status="exhausted", cooldown_until=START - timedelta(seconds=5)),
            None,
        ),  # reset passed: probe it
        (cand(circuit="open", cooldown_until=START + timedelta(seconds=5)), "circuit_open"),
        (cand(circuit="open", cooldown_until=START - timedelta(seconds=5)), None),  # window over: the probe
        (cand(cooldown_until=START + timedelta(seconds=5)), "cooldown"),
        (cand(scope=("client:acme",)), "scope"),
    ],
)
def test_skip_reasons(candidate: Candidate, expected: str | None) -> None:
    assert skip_reason(candidate, "internal", START) == expected


def test_unavailable_reason_rules() -> None:
    later, earlier = START + timedelta(seconds=1), START - timedelta(seconds=1)
    assert health_rules.unavailable_reason("closed", None, START) is None
    assert health_rules.unavailable_reason("closed", later, START) == "cooldown"
    assert health_rules.unavailable_reason("open", later, START) == "circuit_open"
    assert (
        health_rules.unavailable_reason("open", earlier, START) is None
    )  # window over: the next call probes
    assert (
        health_rules.unavailable_reason("open", START, START) is None
    )  # a cooldown that ends exactly now is over


# --- input errors -----------------------------------------------------------------------------------------


async def test_unknown_capability_raises(pool: DbPool, farm_ctx: FarmContext) -> None:
    with pytest.raises(UnknownCapability, match="farm registry sync"):
        await route(farm_ctx, "nope", {}, caller="test")
    assert await fetch(pool, "select 1 from runs") == []  # nothing to record: no capability, no run


async def test_invalid_input_is_rejected_before_any_resource_is_touched(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor()
    ctx = await farm_factory(registry, executors={"api": executor})

    out = await route(ctx, "verify_email", {"email": "not-an-email"}, caller="test")

    assert not out.ok and out.error is not None and out.error.kind == "bad_request"
    assert "email" in out.error.message and out.error.hint
    assert executor.calls == [] and await reservations(pool) == []
    run = await run_row(pool, out.run_id)
    assert (run["status"], run["error_kind"], run["finished"]) == ("failed", "bad_request", True)
    assert await fetch(pool, "select 1 from capability_requests") == []


async def test_invalid_input_never_echoes_the_rejected_value(farm_ctx: FarmContext) -> None:
    out = await route(farm_ctx, "verify_email", {"email": "super-secret-token-123"}, caller="test")
    assert out.error is not None and "super-secret-token-123" not in out.error.message


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"strategy": "bogus"}, "unknown strategy"),
        ({"strategy": "pin"}, "needs a connection id"),
        ({"strategy": "failover", "pin": "reoon-01"}, "cannot be combined"),
        ({"pin": "no-such-connection"}, "there is no connection"),
        ({"pin": "clay-01"}, "not on the route"),
    ],
)
async def test_bad_strategy_or_pin_is_a_bad_request(
    pool: DbPool, farm_factory: Make, registry: Registry, kwargs: dict[str, Any], message: str
) -> None:
    executor = ScriptedExecutor()
    ctx = await farm_factory(registry, executors={"api": executor})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test", **kwargs)

    assert not out.ok and out.error is not None and out.error.kind == "bad_request"
    assert message in out.error.message
    assert executor.calls == []
    assert (await run_row(pool, out.run_id))["status"] == "failed"


# --- the router knows no capability by name ---------------------------------------------------------------


async def test_a_capability_the_router_has_never_heard_of_works_from_the_tables_alone(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.capabilities["lookup_widget"] = CapabilitySpec(
        kind="tool", routes=["reoon"], cache_ttl_seconds=60
    )
    seen: list[ExecRequest] = []

    def answer(req: ExecRequest) -> ExecResult:
        seen.append(req)
        return ExecResult(
            ok=True, data={"widget": req.params["id"], "n": 7}, found=True, units_used={"credits": 1}
        )

    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor(answer)})

    out = await route(ctx, "lookup_widget", {"id": "w-1"}, caller="test")
    again = await route(ctx, "lookup_widget", {"id": "w-1"}, caller="test")

    assert out.ok and out.result == {
        "widget": "w-1",
        "n": 7,
    }  # no catalog entry: params and data pass through
    assert again.source is not None and again.source.cached and len(seen) == 1
    assert (seen[0].capability, seen[0].params) == ("lookup_widget", {"id": "w-1"})


# --- executor misbehaviour --------------------------------------------------------------------------------


async def test_an_answer_that_does_not_match_the_output_model_is_a_failed_attempt(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    garbage = ExecResult(
        ok=True, data={"email": EMAIL, "status": "definitely-valid"}, found=True, units_used={"credits": 1}
    )
    executor = ScriptedExecutor(lambda req: garbage if req.connection.id == "reoon-01" else ok_result())
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.connection_id == "zerobounce-01"
    failure_event = next(d for k, c, d in await events(pool, out.run_id) if k == "failure")
    assert failure_event["error_kind"] == "unknown" and "does not match" in failure_event["error"]
    assert (await health(pool, "reoon-01"))["last_error_kind"] == "unknown"


async def test_an_ok_answer_without_data_is_a_failed_attempt(farm_factory: Make, registry: Registry) -> None:
    ctx = await farm_factory(
        only_reoon_01(registry),
        executors={"api": ScriptedExecutor(ExecResult(ok=True, units_used={"credits": 1}))},
    )
    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    assert not out.ok and out.error is not None and out.error.kind == "unknown"
    assert "no data" in out.error.message


async def test_an_executor_that_raises_is_a_failed_attempt_and_the_next_candidate_answers(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    def crash_on_reoon(req: ExecRequest) -> ExecResult:
        if req.connection.id == "reoon-01":
            raise RuntimeError("adapter bug")
        return ok_result()

    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor(crash_on_reoon)})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.connection_id == "zerobounce-01"
    failure_event = next(d for k, c, d in await events(pool, out.run_id) if k == "failure")
    assert "executor crashed: RuntimeError" in failure_event["error"]
    assert [r[2] for r in await reservations(pool, "reoon-01")] == ["released"]


async def test_a_hanging_provider_is_cut_off_by_the_router_timeout(
    pool: DbPool, farm_factory: Make, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(router, "TIMEOUT_GRACE_S", 0.0)
    registry.providers["reoon"].config["timeout_s"] = 0.2
    executor = ScriptedExecutor(lambda req: ok_result())
    hang = ScriptedExecutor()
    hang.hold = True

    async def execute(req: ExecRequest) -> ExecResult:
        return await (hang if req.connection.id == "reoon-01" else executor).execute(req)

    ctx = await farm_factory(only_reoon_01(registry), executors={"api": AsyncFnExecutor(execute)})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.source is not None and out.source.connection_id == "zerobounce-01"
    failure_event = next(d for k, c, d in await events(pool, out.run_id) if k == "failure")
    assert failure_event["error_kind"] == "timeout" and "router cut the call off" in failure_event["error"]
    assert (await health(pool, "reoon-01"))["last_error_kind"] == "timeout"
    assert await quota(pool, "reoon-01") == (0, 0)  # the hung attempt's reservation was given back


async def test_the_request_deadline_ends_a_slow_chain_and_settles_what_was_reserved(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    hang = ScriptedExecutor()
    hang.hold = True
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": hang}, deadline_s=0.3)

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not out.ok and out.error is not None and out.error.kind == "timeout"
    assert "did not finish within" in out.error.message
    assert [r[2] for r in await reservations(pool)] == ["released"]  # settled even though cancelled mid-call
    assert [r[0] for r in await fetch(pool, "select status from capability_requests")] == ["failed"]
    assert (await run_row(pool, out.run_id))["status"] == "failed" and ctx.flights == {}


async def test_a_failed_call_that_spent_units_is_charged_what_it_reports(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.budgets.per_provider.pop("reoon")
    registry.providers["reoon"].connections[0].units["credits"].unit_cost_usd = Decimal("0.5")
    spent = ExecResult(
        ok=False,
        error_kind=ErrorKind.UNKNOWN,
        error="the model never produced valid output",
        units_used={"credits": 2},
        cost_usd=Decimal("0.003"),
    )
    ctx = await farm_factory(
        only_reoon_01(registry),
        executors={
            "api": ScriptedExecutor(lambda r: spent if r.connection.id == "reoon-01" else ok_result())
        },
    )

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok  # zerobounce answered after reoon failed
    assert await reservations(pool, "reoon-01") == [
        ("reoon-01", "credits", "committed", Decimal(2))
    ]  # not released
    assert out.cost.units == {"credits": 3.0}  # the burnt 2 plus the 1 that bought the answer
    assert out.cost.usd == pytest.approx(1.003)  # 2 x 0.5 + the executor's own 0.003
    usage = await fetch(pool, "select connection_id, amount, cost_usd from usage_events order by id")
    assert usage == [("reoon-01", Decimal(2), Decimal("1.0")), ("zerobounce-01", Decimal(1), Decimal(0))]
    assert Decimal(str((await run_row(pool, out.run_id))["cost_usd"])) == Decimal("1.003")


async def test_attempt_charged_units_cost_the_estimate_even_when_the_call_fails(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.providers["reoon"].connections[0].units["credits"].charged_on = "attempt"
    ctx = await farm_factory(
        only_reoon_01(registry),
        executors={
            "api": ScriptedExecutor(
                lambda r: failure(ErrorKind.SERVER) if r.connection.id == "reoon-01" else ok_result()
            )
        },
    )

    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert await reservations(pool, "reoon-01") == [("reoon-01", "credits", "committed", Decimal(1))]
    assert await quota(pool, "reoon-01") == (1, 0)


# --- empty answers ----------------------------------------------------------------------------------------


def empty_answer(units: dict[str, float] | None = None) -> ExecResult:
    return ExecResult(
        ok=True,
        data={"widget": None},
        found=False,
        error_kind=ErrorKind.EMPTY,
        error="nothing found",
        units_used=units or {},
    )


def found_answer(source: str) -> ExecResult:
    return ExecResult(ok=True, data={"widget": source}, found=True, units_used={"credits": 1})


async def widget_farm(farm_factory: Make, registry: Registry, executor: ScriptedExecutor) -> FarmContext:
    for name in ("find_widget", "lookup_widget"):
        registry.capabilities[name] = CapabilitySpec(kind="tool", routes=["reoon", "zerobounce"])
    return await farm_factory(only_reoon_01(registry), executors={"api": executor})


async def test_find_capabilities_fall_back_on_an_empty_answer_and_the_empty_call_is_not_a_health_failure(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor(
        lambda r: empty_answer() if r.connection.id == "reoon-01" else found_answer("zb")
    )
    ctx = await widget_farm(farm_factory, registry, executor)

    out = await route(ctx, "find_widget", {"q": 1}, caller="test")

    assert (
        out.ok
        and out.result == {"widget": "zb"}
        and [c.connection.id for c in executor.calls] == ["reoon-01", "zerobounce-01"]
    )
    trail = kinds(await events(pool, out.run_id))
    assert (
        trail.index(("success", "reoon-01"))
        < trail.index(("fallback", "reoon-01"))
        < trail.index(("success", "zerobounce-01"))
    )
    assert [r[2:] for r in await reservations(pool, "reoon-01")] == [
        ("released", None)
    ]  # empty + no usage: free
    # an empty answer neither helped nor hurt the account: it has no health record at all
    assert await fetch(pool, "select 1 from connection_health where connection_id = 'reoon-01'") == []


async def test_non_find_capabilities_treat_an_empty_answer_as_the_answer(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor(empty_answer({"credits": 1}))  # the provider billed the empty lookup
    ctx = await widget_farm(farm_factory, registry, executor)

    out = await route(ctx, "lookup_widget", {"q": 1}, caller="test")

    assert out.ok and out.result == {"widget": None} and len(executor.calls) == 1  # no fallback
    assert await reservations(pool, "reoon-01") == [
        ("reoon-01", "credits", "committed", Decimal(1))
    ]  # as reported
    assert await fetch(pool, "select count(*) from capability_requests where status = 'succeeded'") == [(1,)]
    again = await route(ctx, "lookup_widget", {"q": 1}, caller="test")
    assert again.source is not None and not again.source.cached  # an empty answer is not cached


async def test_when_every_pool_answers_empty_the_empty_answer_is_the_result(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor(empty_answer({"credits": 1}))
    ctx = await widget_farm(farm_factory, registry, executor)

    out = await route(ctx, "find_widget", {"q": 1}, caller="test")

    assert out.ok and out.result == {"widget": None} and len(executor.calls) == 2
    assert out.cost.units == {"credits": 2.0}  # both lookups were billed
    assert (await run_row(pool, out.run_id))["status"] == "succeeded"


async def test_an_empty_answer_followed_by_a_failure_is_a_failure_not_nothing_found(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    executor = ScriptedExecutor(
        lambda r: empty_answer() if r.connection.id == "reoon-01" else failure(ErrorKind.SERVER)
    )
    ctx = await widget_farm(farm_factory, registry, executor)

    out = await route(ctx, "find_widget", {"q": 1}, caller="test")

    assert not out.ok and out.error is not None and out.error.kind == "server"
    assert [a.outcome for a in out.error.attempts] == [
        "empty",
        "failed",
    ]  # the agent can see nothing is final


# --- policy, eligibility, capacity ------------------------------------------------------------------------


async def test_a_zero_budget_blocks_a_paid_connection_but_the_free_one_before_it_is_still_used(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.providers["zerobounce"].connections[0].units["credits"].unit_cost_usd = Decimal("0.01")  # paid
    registry.budgets.per_provider["zerobounce"] = Decimal(0)
    executor = ScriptedExecutor(
        lambda r: failure(ErrorKind.SERVER) if r.connection.id == "reoon-01" else ok_result()
    )
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert (
        not out.ok and out.error is not None and out.error.kind == "server"
    )  # reoon failed; zerobounce not tried
    assert [(a.connection_id, a.outcome) for a in out.error.attempts] == [
        ("reoon-01", "failed"),
        ("zerobounce-01", "policy_blocked"),
    ]
    assert [c.connection.id for c in executor.calls] == ["reoon-01"]
    trail = kinds(await events(pool, out.run_id))
    assert ("policy_block", "zerobounce-01") in trail and ("execute", "zerobounce-01") not in trail
    assert (await run_row(pool, out.run_id))["status"] == "failed"  # something was tried, so not 'blocked'


async def test_a_paid_connection_with_a_positive_budget_is_used(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.providers["zerobounce"].connections[0].units["credits"].unit_cost_usd = Decimal("0.01")
    executor = ScriptedExecutor(
        lambda r: failure(ErrorKind.SERVER) if r.connection.id == "reoon-01" else ok_result()
    )
    ctx = await farm_factory(
        only_reoon_01(registry), executors={"api": executor}
    )  # global budget 50, none per provider

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.cost.usd == pytest.approx(0.01)


async def test_when_everything_is_blocked_by_policy_the_run_is_blocked(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    for name in ("reoon", "zerobounce"):
        registry.providers[name].connections[0].units["credits"].unit_cost_usd = Decimal("0.01")
        registry.budgets.per_provider[name] = Decimal(0)
    executor = ScriptedExecutor()
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not out.ok and out.error is not None and out.error.kind == "policy_blocked" and out.error.hint
    assert executor.calls == [] and await reservations(pool) == []
    assert (await run_row(pool, out.run_id))["status"] == "blocked"


async def test_every_connection_skipped_is_no_capacity_with_the_reasons(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    ctx = await farm_factory(registry, executors={"api": ScriptedExecutor()})
    await fetch(
        pool, "update connections set status = 'paused' where id in ('reoon-01', 'reoon-02') returning 1"
    )
    await fetch(pool, "update connections set status = 'needs_login' where id = 'zerobounce-01' returning 1")

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not out.ok and out.error is not None and out.error.kind == "no_capacity"
    assert sorted((a.connection_id, a.kind) for a in out.error.attempts) == [
        ("reoon-01", "paused"),
        ("reoon-02", "paused"),
        ("zerobounce-01", "needs_login"),
    ]
    assert out.source is None  # nothing was tried, so there is no source
    assert (await run_row(pool, out.run_id))["status"] == "failed"


async def test_disabled_provider_and_missing_executor_are_skipped_with_a_reason(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.providers["reoon"].enabled = False
    ctx = await farm_factory(
        only_reoon_01(registry), executors={"llm": ScriptedExecutor()}
    )  # no 'api' executor

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not out.ok
    reasons = [(c, d["reason"]) for k, c, d in await events(pool, out.run_id) if k == "skip"]
    assert reasons == [(None, "provider_disabled"), (None, "no_executor")]


async def test_a_disabled_route_is_not_used(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    executor = ScriptedExecutor()
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})
    await fetch(pool, "update capability_routes set enabled = false where provider_id = 'reoon' returning 1")

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and [c.connection.id for c in executor.calls] == ["zerobounce-01"]


async def test_a_connection_outside_the_callers_scope_is_skipped(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.providers["zerobounce"].connections[0].scope = ["client:acme"]
    ctx = await farm_factory(
        only_reoon_01(registry), executors={"api": ScriptedExecutor(failure(ErrorKind.SERVER))}
    )

    internal = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")
    client = await route(ctx, "verify_email", {"email": "x@example.com"}, caller="test", scope="client:acme")

    assert internal.error is not None and client.error is not None
    skipped = lambda o: [(a.connection_id, a.kind) for a in o.error.attempts if a.outcome == "skipped"]  # noqa: E731
    assert skipped(internal) == [("zerobounce-01", "scope")]
    assert skipped(client) == [("reoon-01", "scope")]


async def test_all_units_of_a_connection_are_reserved_or_none(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    units = registry.providers["reoon"].connections[0].units
    units["zz_actions"] = units["credits"].model_copy(update={"limit": Decimal(0), "charged_on": "attempt"})
    executor = ScriptedExecutor()
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": executor})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and [c.connection.id for c in executor.calls] == [
        "zerobounce-01"
    ]  # reoon could not reserve both
    # 'credits' was reserved first (units are taken in name order) and must have been given back
    assert await reservations(pool, "reoon-01") == [("reoon-01", "credits", "released", None)]
    assert await quota(pool, "reoon-01", "credits") == (0, 0)
    assert ("reserve_failed", "reoon-01") in kinds(await events(pool, out.run_id))


# --- health transitions -----------------------------------------------------------------------------------


async def test_five_consecutive_server_errors_open_the_circuit_for_two_minutes_then_one_probe_closes_it(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    clock = FakeClock()
    healthy = {"reoon-01": False}

    def answer(req: ExecRequest) -> ExecResult:
        if req.connection.id == "reoon-01" and not healthy["reoon-01"]:
            return failure(ErrorKind.SERVER)
        return ok_result()

    executor = ScriptedExecutor(answer)
    ctx = await farm_factory(only_reoon_01(registry), clock=clock, executors={"api": executor})

    for i in range(5):
        await route(ctx, "verify_email", {"email": f"user{i}@example.com"}, caller="test")
    h = await health(pool, "reoon-01")
    assert (h["circuit"], h["consecutive_failures"], h["cooldown_until"]) == (
        "open",
        5,
        START + timedelta(seconds=120),
    )
    calls_to_reoon = sum(c.connection.id == "reoon-01" for c in executor.calls)
    assert calls_to_reoon == 5

    out = await route(ctx, "verify_email", {"email": "later@example.com"}, caller="test")
    assert [(c, d["reason"]) for k, c, d in await events(pool, out.run_id) if k == "skip"] == [
        ("reoon-01", "circuit_open")
    ]
    assert sum(c.connection.id == "reoon-01" for c in executor.calls) == 5  # not called while open

    clock.advance(121)
    healthy["reoon-01"] = True
    probe = await route(ctx, "verify_email", {"email": "probe@example.com"}, caller="test")
    assert probe.source is not None and probe.source.connection_id == "reoon-01"
    h = await health(pool, "reoon-01")
    assert (h["circuit"], h["consecutive_failures"], h["cooldown_until"]) == ("closed", 0, None)


async def test_a_failed_probe_reopens_the_circuit_immediately(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    clock = FakeClock()
    ctx = await farm_factory(
        only_reoon_01(registry),
        clock=clock,
        executors={
            "api": ScriptedExecutor(
                lambda r: failure(ErrorKind.TIMEOUT) if r.connection.id == "reoon-01" else ok_result()
            )
        },
    )
    for i in range(5):
        await route(ctx, "verify_email", {"email": f"u{i}@example.com"}, caller="test")
    clock.advance(121)

    await route(ctx, "verify_email", {"email": "probe@example.com"}, caller="test")

    h = await health(pool, "reoon-01")
    assert h["circuit"] == "open" and h["consecutive_failures"] == 6
    assert h["cooldown_until"] == clock.now + timedelta(seconds=120)


async def test_limit_reached_exhausts_the_account_until_its_reset_then_a_success_revives_it(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    clock = FakeClock()
    script: dict[str, ExecResult] = {"reoon-01": failure(ErrorKind.LIMIT_REACHED, "out of credits")}
    executor = ScriptedExecutor(lambda r: script.get(r.connection.id) or ok_result())
    ctx = await farm_factory(only_reoon_01(registry), clock=clock, executors={"api": executor})

    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert await status_of(pool, "reoon-01") == "exhausted"
    cooldown = (await health(pool, "reoon-01"))["cooldown_until"]
    assert cooldown == START.replace(hour=0, minute=0) + timedelta(
        days=1
    )  # reoon-01's credits reset daily (UTC)

    blocked = await route(ctx, "verify_email", {"email": "b@example.com"}, caller="test")
    assert [(c, d["reason"]) for k, c, d in await events(pool, blocked.run_id) if k == "skip"] == [
        ("reoon-01", "exhausted")
    ]

    clock.now = cooldown + timedelta(seconds=1)
    del script["reoon-01"]
    revived = await route(ctx, "verify_email", {"email": "c@example.com"}, caller="test")
    assert revived.source is not None and revived.source.connection_id == "reoon-01"
    assert await status_of(pool, "reoon-01") == "active"


async def test_a_paused_account_is_not_flipped_by_a_late_limit_error(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    started_and_paused = ScriptedExecutor(
        lambda r: failure(ErrorKind.LIMIT_REACHED) if r.connection.id == "reoon-01" else ok_result()
    )

    async def pause_then_fail(req: ExecRequest) -> ExecResult:
        await fetch(
            pool, "update connections set status = 'paused' where id = 'reoon-01' returning 1"
        )  # the owner pauses it mid-call
        return await started_and_paused.execute(req)

    ctx = await farm_factory(only_reoon_01(registry), executors={"api": AsyncFnExecutor(pause_then_fail)})

    await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert await status_of(pool, "reoon-01") == "paused"  # the owner's decision stands


# --- bookkeeping, secrets, crash handling -----------------------------------------------------------------


async def test_a_bookkeeping_error_never_throws_away_a_paid_answer(
    pool: DbPool, farm_factory: Make, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor()})

    async def broken_commit(*args: Any, **kwargs: Any) -> None:
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(ledger, "commit", broken_commit)

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.ok and out.result is not None  # the caller still gets what the provider charged for
    assert [r[2] for r in await reservations(pool, "reoon-01")] == ["reserved"]
    # ...and the reservation is not stuck forever: expiry returns it to the pool
    await fetch(pool, "update quota_reservations set expires_at = now() - interval '1 second' returning 1")
    assert await ledger.expire(pool) == 1
    assert await quota(pool, "reoon-01") == (0, 0)


async def test_an_unexpected_error_finalises_the_run_and_the_request_row_and_comes_back_as_an_envelope(
    pool: DbPool, farm_factory: Make, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor()})

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bug in the budget loader")

    monkeypatch.setattr(router, "_load_budgets", broken)

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not out.ok and out.error is not None and out.error.kind == "internal"
    assert "bug in the budget loader" in out.error.message and out.error.hint
    rows = await fetch(pool, "select id, status, error_kind, finished_at is not null from runs")
    assert rows == [(out.run_id, "failed", "internal", True)]  # not left 'running', and the envelope names it
    assert [r[0] for r in await fetch(pool, "select status from capability_requests")] == [
        "failed"
    ]  # waiters free
    assert ctx.flights == {}


async def test_concurrent_callers_of_a_crashed_execution_all_get_an_internal_envelope(
    pool: DbPool, farm_factory: Make, registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor()})

    async def broken(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.1)
        raise RuntimeError("bug")

    monkeypatch.setattr(router, "_load_budgets", broken)

    outs = await asyncio.gather(
        *(route(ctx, "verify_email", {"email": EMAIL}, caller="test") for _ in range(4))
    )

    assert all(not o.ok and o.error is not None and o.error.kind == "internal" for o in outs)
    assert [r[0] for r in await fetch(pool, "select distinct status from runs")] == [
        "failed"
    ]  # none left running


async def test_no_secret_reaches_the_database(pool: DbPool, farm_factory: Make, registry: Registry) -> None:
    secret = "SENTINEL-secret-value-8d41c0"
    register_secret(secret)
    leaky = failure(
        ErrorKind.SERVER, f"GET https://provider.example/verify?key={secret} failed", retry_after_s=None
    )
    ctx = await farm_factory(
        only_reoon_01(registry),
        executors={
            "api": ScriptedExecutor(
                lambda r: (
                    leaky if r.connection.id == "reoon-01" else failure(ErrorKind.AUTH, f"bad key {secret}")
                )
            )
        },
    )

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert not out.ok and secret not in out.model_dump_json()
    for table in (
        "run_events",
        "runs",
        "connection_health",
        "capability_requests",
        "usage_events",
        "audit_events",
    ):
        rows = await fetch(pool, f"select t::text from {table} t")
        assert not any(secret in r[0] for r in rows), table


async def test_a_crash_is_logged_with_its_traceback_but_never_with_a_secret(
    pool: DbPool, farm_factory: Make, registry: Registry, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "SENTINEL-log-secret-3c9e17"
    register_secret(secret)

    def crash(req: ExecRequest) -> ExecResult:
        raise RuntimeError(f"GET https://provider.example/verify?key={secret} blew up")

    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor(crash)})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    logged = capsys.readouterr()
    text = logged.out + logged.err
    assert not out.ok and "router.executor_crashed" in text and "RuntimeError" in text and "crash" in text
    assert secret not in text and secret not in out.model_dump_json()


async def test_a_pool_without_connections_is_reported_not_silently_empty(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    registry.providers["zerobounce"].connections = []
    ctx = await farm_factory(
        only_reoon_01(registry), executors={"api": ScriptedExecutor(failure(ErrorKind.SERVER))}
    )

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test")

    assert out.error is not None
    assert [(a.provider, a.outcome, a.kind) for a in out.error.attempts] == [
        ("reoon", "failed", "server"),
        ("zerobounce", "skipped", "no_connections"),
    ]


async def test_events_are_numbered_from_one_without_gaps(pool: DbPool, farm_ctx: FarmContext) -> None:
    farm_ctx.executors["api"] = ScriptedExecutor()
    out = await route(farm_ctx, "verify_email", {"email": EMAIL}, caller="test")
    seqs = [
        r[0]
        for r in await fetch(pool, "select seq from run_events where run_id = %s order by id", out.run_id)
    ]
    assert seqs == list(range(1, len(seqs) + 1)) and len(seqs) >= 5
    plan = (await events(pool, out.run_id))[0]
    assert plan[0] == "plan" and plan[2]["routes"] == ["reoon", "zerobounce"] and plan[2]["caller"] == "test"


async def test_the_candidate_event_explains_a_strategy_it_cannot_honour_yet(
    pool: DbPool, farm_factory: Make, registry: Registry
) -> None:
    ctx = await farm_factory(only_reoon_01(registry), executors={"api": ScriptedExecutor()})

    out = await route(ctx, "verify_email", {"email": EMAIL}, caller="test", strategy="round_robin")

    candidate = next(d for k, _, d in await events(pool, out.run_id) if k == "candidate")
    assert candidate["strategy"] == "failover" and "round_robin" in candidate["strategy_note"]


async def test_a_pinned_connection_still_obeys_eligibility(pool: DbPool, farm_ctx: FarmContext) -> None:
    farm_ctx.executors["api"] = executor = ScriptedExecutor()
    await fetch(pool, "update connections set status = 'paused' where id = 'reoon-02' returning 1")

    out = await route(farm_ctx, "verify_email", {"email": EMAIL}, caller="test", pin="reoon-02")

    assert not out.ok and out.error is not None and out.error.kind == "no_capacity"
    assert [(a.connection_id, a.kind) for a in out.error.attempts] == [("reoon-02", "paused")]
    assert executor.calls == []  # a pin does not fall back to some other account


async def test_the_envelope_is_json_safe_and_has_the_contract_keys(farm_ctx: FarmContext) -> None:
    farm_ctx.executors["api"] = ScriptedExecutor()
    out = await route(farm_ctx, "verify_email", {"email": EMAIL}, caller="test")

    envelope = json.loads(json.dumps(out.envelope()))

    assert set(envelope) == {"ok", "result", "error", "run_id", "source", "cost"}
    assert set(envelope["source"]) == {"provider", "connection_id", "cached"}
    assert set(envelope["cost"]) == {"usd", "units"} and envelope["error"] is None
