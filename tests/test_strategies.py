"""Strategies are pure functions: same candidates in, same order out."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from farm.registry.models import STRATEGIES
from farm.resources.strategies import IMPLEMENTED, order_candidates, resolve_strategy


@dataclass(frozen=True)
class C:
    id: str
    priority: int


POOL = [C("b", 2), C("c", 3), C("a2", 1), C("a1", 1)]


def test_failover_orders_by_priority_then_id() -> None:
    assert [c.id for c in order_candidates(POOL, "failover")] == ["a1", "a2", "b", "c"]


def test_failover_is_deterministic_whatever_the_input_order() -> None:
    assert order_candidates(POOL, "failover") == order_candidates(list(reversed(POOL)), "failover")


def test_failover_does_not_mutate_its_input() -> None:
    before = list(POOL)
    order_candidates(POOL, "failover")
    assert POOL == before


def test_pin_returns_only_the_pinned_connection() -> None:
    assert order_candidates(POOL, "pin", "c") == [C("c", 3)]


def test_pin_to_a_connection_that_is_not_a_candidate_is_empty_not_an_error() -> None:
    assert order_candidates(POOL, "pin", "zzz") == []


def test_pin_without_an_id_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="needs a connection id"):
        order_candidates(POOL, "pin")


def test_unimplemented_strategies_refuse_loudly() -> None:
    with pytest.raises(ValueError, match="not implemented"):
        order_candidates(POOL, "round_robin")


def test_empty_pool_orders_to_empty() -> None:
    assert order_candidates([], "failover") == []


@pytest.mark.parametrize(
    ("requested", "pin", "capability", "provider", "expected"),
    [
        (None, None, None, None, "failover"),
        (None, None, "failover", "sticky", "failover"),  # capability default beats the provider's
        (None, None, None, "failover", "failover"),
        ("failover", None, "failover", None, "failover"),
        (None, "reoon-01", "failover", None, "pin"),
        ("pin", "reoon-01", None, None, "pin"),
    ],
)
def test_resolution_order(
    requested: str | None, pin: str | None, capability: str | None, provider: str | None, expected: str
) -> None:
    resolved = resolve_strategy(requested=requested, pin=pin, capability=capability, provider=provider)
    assert resolved.name == expected and resolved.note is None


@pytest.mark.parametrize("name", [s for s in STRATEGIES if s not in IMPLEMENTED])
def test_a_registry_strategy_that_is_not_built_yet_is_served_by_failover_and_says_so(name: str) -> None:
    resolved = resolve_strategy(requested=name, pin=None, capability=None, provider=None)
    assert resolved.name == "failover"
    assert resolved.note is not None and name in resolved.note


@pytest.mark.parametrize(
    ("requested", "pin", "message"),
    [
        ("bogus", None, "unknown strategy"),
        ("pin", None, "needs a connection id"),
        ("failover", "reoon-01", "cannot be combined"),
    ],
)
def test_caller_mistakes_raise(requested: str, pin: str | None, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        resolve_strategy(requested=requested, pin=pin, capability=None, provider=None)
