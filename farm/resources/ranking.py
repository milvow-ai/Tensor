"""Candidate ranking inside a pool (HANDOFF 4.3).

Pure functions that score and order eligible candidates by:
(a) price class: free (unit_cost 0) < pay-only-if-found (charged_on found) < cheapest per unit;
(b) soonest reset first among equal price (spend credits that expire first);
(c) success rate over the last 50 calls (from connection_health counts);
(d) priority (lower is first);
(e) id (alphabetical tie-break).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol


class Rankable(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def priority(self) -> int: ...


@dataclass(frozen=True)
class RankingResult[C]:
    candidates: list[C]
    reasons: dict[str, str]

    def __iter__(self) -> Iterator[C]:
        return iter(self.candidates)

    def __getitem__(self, idx: int) -> C:
        return self.candidates[idx]

    def __len__(self) -> int:
        return len(self.candidates)


def _price_class_and_cost(candidate: Any) -> tuple[int, Decimal, str]:
    """Classify candidate into:
    0: Free (all units unit_cost_usd == 0, or no units)
    1: Pay-only-if-found (all non-zero units charged_on == 'found')
    2: Paid (any non-zero unit charged_on 'attempt' or 'success')
    """
    units = getattr(candidate, "units", ())
    if not units:
        return (0, Decimal(0), "free")

    costs = [Decimal(str(getattr(u, "unit_cost_usd", 0))) for u in units]
    if all(c <= 0 for c in costs):
        return (0, Decimal(0), "free")

    paid_units = [u for u in units if Decimal(str(getattr(u, "unit_cost_usd", 0))) > 0]
    min_cost = min(Decimal(str(getattr(u, "unit_cost_usd", 0))) for u in paid_units)

    if all(getattr(u, "charged_on", "attempt") == "found" for u in paid_units):
        return (1, min_cost, f"pay-only-if-found (${min_cost:g}/unit)")

    return (2, min_cost, f"paid (${min_cost:g}/unit)")


def _soonest_reset(candidate: Any) -> tuple[tuple[int, datetime], str]:
    """Earliest next_reset_at among units: (0, earliest) if known, else (1, max_dt)."""
    units = getattr(candidate, "units", ())
    resets: list[datetime] = []
    for u in units:
        reset_at = getattr(u, "next_reset_at", None)
        if reset_at is not None:
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=UTC)
            resets.append(reset_at)

    if resets:
        earliest = min(resets)
        return ((0, earliest), earliest.isoformat())
    return ((1, datetime.max.replace(tzinfo=UTC)), "none")


def _success_rate(candidate: Any) -> tuple[float, str]:
    """Success rate (0.0 to 1.0, default 1.0 for new accounts) from connection_health counts."""
    success = int(getattr(candidate, "success_count", 0) or 0)
    failure = int(getattr(candidate, "failure_count", 0) or 0)
    total = success + failure
    if total == 0:
        return (1.0, "100% (new)")
    rate = float(success) / float(total)
    return (rate, f"{rate:.0%} ({success}/{total})")


def score_candidate(candidate: Any) -> tuple[tuple[Any, ...], str]:
    """Return the sorting key tuple and human-readable reason for ranking."""
    price_class, unit_cost, price_desc = _price_class_and_cost(candidate)
    reset_key, reset_desc = _soonest_reset(candidate)
    rate, rate_desc = _success_rate(candidate)
    priority = int(getattr(candidate, "priority", 100))
    cid = str(getattr(candidate, "id", ""))

    key = (
        price_class,
        unit_cost,
        reset_key,
        -rate,
        priority,
        cid,
    )
    reason = (
        f"price: {price_desc}, reset: {reset_desc}, "
        f"success: {rate_desc}, priority: {priority}"
    )
    return (key, reason)


def rank_candidates[C: Rankable](candidates: Sequence[C]) -> RankingResult[C]:
    """Order candidates by price class -> soonest reset -> success rate -> priority -> id."""
    scored: list[tuple[tuple[Any, ...], str, C]] = []
    for c in candidates:
        key, reason = score_candidate(c)
        scored.append((key, reason, c))

    scored.sort(key=lambda item: item[0])

    ordered = [item[2] for item in scored]
    reasons = {item[2].id: item[1] for item in scored}
    return RankingResult(candidates=ordered, reasons=reasons)
