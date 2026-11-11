"""Authenticated operator provenance; client branding is kept separate from person identity."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from farm.tenancy.context import current_user, require_tenant

_recorded: ContextVar[tuple[UUID | None, str | None, str | None] | None] = ContextVar(
    "farm_recorded_operator", default=None
)


@contextmanager
def recorded_operator(user: UUID | None, email: str | None, client: str | None) -> Iterator[None]:
    from farm.gateway.middleware import current_identity

    token = _recorded.set((user, email, client))
    identity = current_identity.set(None)  # never inherit a different queued caller's approval authority
    try:
        yield
    finally:
        current_identity.reset(identity)
        _recorded.reset(token)


def operator_snapshot() -> tuple[UUID | None, str | None, str | None]:
    from farm.gateway.middleware import current_identity

    recorded = _recorded.get()
    if recorded is not None and recorded[0] == current_user.get():
        return recorded
    identity = current_identity.get()
    if identity is None or identity.tenant_id != require_tenant() or identity.user_id != current_user.get():
        return None, None, None
    return identity.user_id, identity.user_email, identity.name
