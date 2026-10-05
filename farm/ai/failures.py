"""Failures of AI jobs: the kinds a caller sees, and how a router failure becomes one of them.

A caller that fans work out to several workers must be able to tell, per worker, what went wrong and what to
do about it. ``classify`` maps the router's error (``farm.resources.router.RouteError``) to the small set of
kinds of ``farm.capabilities.schemas.FailureKind``:

=======================  ===============================================================================
kind                     meaning / what the caller does
=======================  ===============================================================================
``limit``                the account hit its usage limit; ``retry_at`` says when it is back
``auth``                 the account is logged out; the owner must run ``farm ai login <account>``
``timeout``              the worker did not finish within ``timeout_s``
``crash``                the CLI died or answered nonsense (server/unknown/internal errors)
``bad_request``          the request cannot work as given (bad model, edit outside the allowed roots, ...)
``cancelled``            ``ai_cancel`` stopped it
``farm_restart``         the Farm process that ran it stopped; it was not re-run
``account_unavailable``  the account cannot take the job now (paused, cooling down, limited, logged out);
                         a conversation never moves to another account, so the caller waits for ``retry_at``
=======================  ===============================================================================

A reply (a turn on an existing native session) reports a limit or a logout of its account as
``account_unavailable`` with the underlying reason in ``cause``: the session lives on that account, so the
only way on is to wait for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from farm.capabilities.schemas import FailureKind, RequestErrorKind
from farm.resources.router import RouteError

BUSY_SKIPS = frozenset({"concurrency_limit", "saturated"})
"""Skip reasons the router gives when another call holds the account's gate: nothing ran, so the job waits."""

_ACCOUNT_STATE_KINDS = frozenset({"no_capacity", "session_account_unavailable"})
_KIND_OF_ROUTER_KIND: dict[str, FailureKind] = {
    "limit_reached": "limit",
    "rate_limited": "limit",
    "auth": "auth",
    "needs_login": "auth",
    "timeout": "timeout",
    "bad_request": "bad_request",
    "session_unknown": "bad_request",
    "policy_blocked": "account_unavailable",
}
_REASON_IN_PARENS = re.compile(r"\(([a-z_]+)\)\s*$")
MAX_MESSAGE_CHARS = 500


class AiRequestError(Exception):
    """The Farm refuses a request before any worker runs (the tool reports it as ``ok = false``)."""

    def __init__(
        self,
        kind: RequestErrorKind,
        message: str,
        *,
        ai: str | None = None,
        account: str | None = None,
        retry_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: RequestErrorKind = kind
        self.message = message
        self.ai = ai
        self.account = account
        self.retry_at = retry_at


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    message: str
    cause: str | None = None


@dataclass(frozen=True)
class Busy:
    """Nothing ran: another call held the account's gate. The job goes back to the queue and tries again."""


def classify(error: RouteError, *, account: str, replying: bool) -> Failure | Busy:
    """What a router error means for the job that was pinned to ``account``.

    ``replying``: the job resumes a native session, which lives on ``account`` and nowhere else.
    """
    skips = [a for a in error.attempts if a.outcome == "skipped"]
    if error.kind in _ACCOUNT_STATE_KINDS and skips and len(skips) == len(error.attempts):
        if all(a.kind in BUSY_SKIPS for a in skips):
            return Busy()

    kind: FailureKind = _KIND_OF_ROUTER_KIND.get(error.kind, "crash")
    cause: str | None = None
    message = error.message
    if error.kind in _ACCOUNT_STATE_KINDS:
        quota = [a for a in error.attempts if a.outcome == "reserve_failed"]
        if quota:
            kind, cause, message = "limit", "quota", f"{quota[0].message} (the account's quota is used up)"
        elif any(a.kind == "model_not_offered" for a in skips):
            kind = "bad_request"
        else:
            kind = "account_unavailable"
            matched = _REASON_IN_PARENS.search(error.message)
            cause = ",".join(sorted({a.kind for a in skips})) or (matched.group(1) if matched else None)
    elif error.kind == "policy_blocked":
        cause = "policy_blocked"

    if kind == "auth":
        message = f"{message} Log the account in again with: farm ai login {account}"
    if replying and kind in ("limit", "auth"):
        return Failure("account_unavailable", message[:MAX_MESSAGE_CHARS], cause=kind)
    return Failure(kind, message[:MAX_MESSAGE_CHARS], cause=cause)


def retryable_on_another_account(kind: FailureKind) -> bool:
    """The failures ``retry_other_account`` reruns: the account could not do the job, the job is fine."""
    return kind in ("limit", "auth", "crash", "account_unavailable")
