"""Multi-account strategies (HANDOFF 4.3): how the connections of one pool are ordered for a request.

M1 implements ``failover`` (lowest ``priority`` first, so one account is used until it cannot serve, then the
next) and ``pin`` (exactly the connection the caller named). The other strategies of the registry
(``most_remaining``, ``round_robin``, ``parallel_split``, ``sticky``, ``fit_check``) arrive with M2/M3; until
then a request that asks for one is served with ``failover`` and the router records that in the run's ``plan``
event (``resolve_strategy`` returns the note), so the substitution is visible and never silent.

Everything here is pure: no I/O, no clock, deterministic for equal input.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from farm.registry.models import STRATEGIES

IMPLEMENTED: tuple[str, ...] = ("failover", "pin")
FALLBACK_STRATEGY = "failover"


class Rankable(Protocol):
    """What a strategy needs to know about a candidate connection."""

    @property
    def id(self) -> str: ...

    @property
    def priority(self) -> int: ...


@dataclass(frozen=True)
class ResolvedStrategy:
    name: str
    note: str | None = None
    """Why ``name`` differs from what was asked for (None when it does not)."""


def resolve_strategy(
    *,
    requested: str | None,
    pin: str | None,
    capability: str | None,
    provider: str | None,
) -> ResolvedStrategy:
    """Pick the strategy for a pool: ``pin`` > caller's choice > capability default > provider default.

    Raises ``ValueError`` for a name the registry does not know, and for ``pin`` without a connection id
    (or a connection id with a different explicit strategy): those are caller mistakes, not routing facts.
    """
    if pin is not None:
        if requested not in (None, "pin"):
            raise ValueError(f"strategy {requested!r} cannot be combined with pin={pin!r}")
        return ResolvedStrategy("pin")
    if requested == "pin":
        raise ValueError("strategy 'pin' needs a connection id (pin=<connection id>)")
    name = requested or capability or provider or FALLBACK_STRATEGY
    if name not in STRATEGIES:
        raise ValueError(f"unknown strategy {name!r}; known: {', '.join(STRATEGIES)}")
    if name not in IMPLEMENTED:
        note = f"strategy {name!r} is not implemented yet; using {FALLBACK_STRATEGY}"
        return ResolvedStrategy(FALLBACK_STRATEGY, note)
    return ResolvedStrategy(name)


def order_candidates[C: Rankable](candidates: Sequence[C], strategy: str, pin: str | None = None) -> list[C]:
    """Order ``candidates`` (already filtered for eligibility) for ``strategy``.

    * ``failover``: ascending ``priority``, ties broken by connection id so the order is stable.
    * ``pin``: only the connection named by ``pin``; empty when it is not among the candidates.
    """
    if strategy == "failover":
        return sorted(candidates, key=lambda c: (c.priority, c.id))
    if strategy == "pin":
        if pin is None:
            raise ValueError("strategy 'pin' needs a connection id")
        return [c for c in candidates if c.id == pin]
    raise ValueError(f"strategy {strategy!r} is not implemented (implemented: {', '.join(IMPLEMENTED)})")
