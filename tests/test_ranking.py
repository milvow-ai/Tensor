"""Unit tests for candidate ranking inside a pool (farm/resources/ranking.py)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from farm.resources.ranking import rank_candidates


@dataclass(frozen=True)
class DummyUnit:
    unit: str = "credits"
    unit_cost_usd: Decimal = Decimal(0)
    charged_on: str = "attempt"
    next_reset_at: datetime | None = None


@dataclass(frozen=True)
class DummyCandidate:
    id: str
    priority: int = 100
    units: tuple[DummyUnit, ...] = field(default_factory=tuple)
    success_count: int = 0
    failure_count: int = 0


def test_ranking_price_class_ordering() -> None:
    # free < pay-only-if-found < paid
    free = DummyCandidate("c-free", priority=100, units=(DummyUnit(unit_cost_usd=Decimal(0)),))
    found = DummyCandidate(
        "c-found", priority=10, units=(DummyUnit(unit_cost_usd=Decimal("0.05"), charged_on="found"),)
    )
    paid = DummyCandidate(
        "c-paid", priority=1, units=(DummyUnit(unit_cost_usd=Decimal("0.01"), charged_on="attempt"),)
    )

    result = rank_candidates([paid, free, found])
    assert [c.id for c in result.candidates] == ["c-free", "c-found", "c-paid"]
    assert "free" in result.reasons["c-free"]
    assert "pay-only-if-found" in result.reasons["c-found"]
    assert "paid" in result.reasons["c-paid"]


def test_ranking_cheapest_per_unit_inside_paid() -> None:
    cheap = DummyCandidate(
        "c-cheap", units=(DummyUnit(unit_cost_usd=Decimal("0.005"), charged_on="attempt"),)
    )
    expensive = DummyCandidate(
        "c-exp", units=(DummyUnit(unit_cost_usd=Decimal("0.01"), charged_on="attempt"),)
    )

    result = rank_candidates([expensive, cheap])
    assert [c.id for c in result.candidates] == ["c-cheap", "c-exp"]


def test_ranking_soonest_reset_wins_among_equal_price() -> None:
    now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    reset_early = DummyCandidate(
        "c-early",
        units=(DummyUnit(unit_cost_usd=Decimal("0.01"), next_reset_at=now + timedelta(hours=2)),),
    )
    reset_late = DummyCandidate(
        "c-late",
        units=(DummyUnit(unit_cost_usd=Decimal("0.01"), next_reset_at=now + timedelta(days=2)),),
    )
    no_reset = DummyCandidate(
        "c-no-reset",
        units=(DummyUnit(unit_cost_usd=Decimal("0.01"), next_reset_at=None),),
    )

    result = rank_candidates([no_reset, reset_late, reset_early])
    assert [c.id for c in result.candidates] == ["c-early", "c-late", "c-no-reset"]


def test_ranking_success_rate_wins_among_equal_price_and_reset() -> None:
    # 45/50 = 90%
    high_success = DummyCandidate(
        "c-high",
        units=(DummyUnit(unit_cost_usd=Decimal("0.01")),),
        success_count=45,
        failure_count=5,
    )
    # 25/50 = 50%
    low_success = DummyCandidate(
        "c-low",
        units=(DummyUnit(unit_cost_usd=Decimal("0.01")),),
        success_count=25,
        failure_count=25,
    )
    # 0/0 = 100% default
    brand_new = DummyCandidate(
        "c-new",
        units=(DummyUnit(unit_cost_usd=Decimal("0.01")),),
        success_count=0,
        failure_count=0,
    )

    result = rank_candidates([low_success, brand_new, high_success])
    assert [c.id for c in result.candidates] == ["c-new", "c-high", "c-low"]


def test_ranking_priority_and_id_tie_breakers() -> None:
    c1 = DummyCandidate("c-b", priority=10)
    c2 = DummyCandidate("c-a", priority=10)
    c3 = DummyCandidate("c-z", priority=5)

    # Priority 5 comes first; among priority 10, 'c-a' < 'c-b' alphabetically
    result = rank_candidates([c1, c2, c3])
    assert [c.id for c in result.candidates] == ["c-z", "c-a", "c-b"]
