"""Multi-account strategies (HANDOFF 4.3): how the connections of one pool are ordered for a request.

Strategies implemented:
- ``failover``: ranked order (price class -> reset date -> success rate -> priority -> id).
- ``most_remaining``: highest remaining quota fraction first, ties broken by ranked order.
- ``round_robin``: persistent cursor per pool in Postgres (pool_state table), skips ineligible.
- ``pin``: exactly the connection the caller named.
- ``parallel_split`` / ``sticky`` / ``fit_check``: stay for M3 (raise NotImplementedError).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from farm.db.pool import DbPool
from farm.registry.models import STRATEGIES
from farm.resources.ranking import Rankable, rank_candidates, score_candidate

IMPLEMENTED: tuple[str, ...] = ("failover", "most_remaining", "round_robin", "pin")
UNIMPLEMENTED_M3: tuple[str, ...] = ("parallel_split", "sticky", "fit_check")
FALLBACK_STRATEGY = "failover"


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

    Raises ``NotImplementedError`` for strategies reserved for M3 (parallel_split, sticky, fit_check).
    Raises ``ValueError`` for unknown names or invalid combinations.
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


def order_candidates[C: Rankable](
    candidates: Sequence[C],
    strategy: str,
    pin: str | None = None,
    *,
    cursor: int = 0,
    remaining_fractions: Mapping[str, float] | None = None,
) -> list[C]:
    """Order ``candidates`` (already filtered for eligibility) for ``strategy``.

    * ``failover``: ranked order via `rank_candidates`.
    * ``most_remaining``: highest remaining fraction first.
    * ``round_robin``: rotated by cursor.
    * ``pin``: only the connection named by ``pin``.
    """
    if not candidates:
        return []

    if strategy == "failover":
        return rank_candidates(candidates).candidates

    if strategy == "most_remaining":
        def fraction(c: C) -> float:
            if remaining_fractions is not None and c.id in remaining_fractions:
                return remaining_fractions[c.id]
            f = getattr(c, "remaining_fraction", None)
            return float(f) if f is not None else 1.0

        # Sort by (-fraction, rank_sort_key)
        return sorted(candidates, key=lambda c: (-fraction(c), score_candidate(c)[0]))

    if strategy == "round_robin":
        idx = cursor % len(candidates)
        return list(candidates[idx:]) + list(candidates[:idx])

    if strategy == "pin":
        if pin is None:
            raise ValueError("strategy 'pin' needs a connection id")
        return [c for c in candidates if c.id == pin]

    if strategy in UNIMPLEMENTED_M3:
        raise NotImplementedError(f"strategy {strategy!r} is not implemented yet; stays for M3")

    raise ValueError(f"strategy {strategy!r} is not implemented (implemented: {', '.join(IMPLEMENTED)})")


async def get_and_advance_rr_cursor(pool: DbPool, provider_id: str, count: int) -> int:
    """Get the current round-robin cursor for provider_id and advance it by 1, atomically."""
    if count <= 0:
        return 0
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            """
            insert into public.pool_state (provider_id, rr_cursor, updated_at)
            values (%s, 0, now())
            on conflict (provider_id) do nothing
            """,
            (provider_id,),
        )
        cur = await conn.execute(
            """
            select rr_cursor
            from public.pool_state
            where provider_id = %s
            for update
            """,
            (provider_id,),
        )
        row = await cur.fetchone()
        cursor = int(row[0]) if row and row[0] is not None else 0
        new_cursor = (cursor + 1) % count
        await conn.execute(
            """
            update public.pool_state
            set rr_cursor = %s,
                updated_at = now()
            where provider_id = %s
            """,
            (new_cursor, provider_id),
        )
        return cursor
