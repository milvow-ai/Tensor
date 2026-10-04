"""Quota period arithmetic in Python, the mirror of the SQL function ``farm_period_start`` (migration 0001).

The ledger decides which ``quota_usage`` row a reservation lands on in SQL; the router and the capacity report
need the same boundaries in Python (when does an exhausted connection come back, when does a meter reset).
``tests/test_periods.py`` checks both implementations against each other on random timestamps, so they cannot
drift apart silently. All boundaries are UTC; ``rolling_5h`` / ``total`` / ``none`` have no boundary.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
"""Fixed ``period_start`` of the periods without boundaries (one ``quota_usage`` row for all time)."""

_UNBOUNDED = frozenset({"rolling_5h", "total", "none"})
_FIXED = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
}


def _month_start(year: int, month: int, anchor: int) -> datetime:
    """The anchor day of the month, clamped to the month length (anchor 31 -> Feb 28/29, Apr 30, ...)."""
    return datetime(year, month, min(anchor, calendar.monthrange(year, month)[1]), tzinfo=UTC)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def period_start(period: str, anchor: int | None, at: datetime) -> datetime:
    """Start of the quota period that contains ``at`` (same rules as SQL ``farm_period_start``)."""
    at = at.astimezone(UTC)
    if period in _UNBOUNDED:
        return EPOCH
    if period == "minute":
        return at.replace(second=0, microsecond=0)
    if period == "hour":
        return at.replace(minute=0, second=0, microsecond=0)
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return midnight
    if period == "week":  # ISO week: starts Monday
        return midnight - timedelta(days=at.weekday())
    if period == "month":
        day = max(anchor or 1, 1)
        start = _month_start(at.year, at.month, day)
        if at < start:
            start = _month_start(*_shift_month(at.year, at.month, -1), day)
        return start
    raise ValueError(f"unknown period {period!r}")


def next_period_start(period: str, anchor: int | None, at: datetime) -> datetime | None:
    """When the period containing ``at`` ends (= the next period's start); ``None`` if it never resets."""
    if period in _UNBOUNDED:
        return None
    start = period_start(period, anchor, at)
    step = _FIXED.get(period)
    if step is not None:
        return start + step
    # month: the next boundary is the anchor day of the following calendar month (clamped), which is later
    # than ``start`` even when ``start`` itself was clamped (Feb 28 -> Mar 31 for anchor 31).
    year, month = _shift_month(start.year, start.month, 1)
    return _month_start(year, month, max(anchor or 1, 1))
