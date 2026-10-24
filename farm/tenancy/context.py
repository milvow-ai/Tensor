"""The tenant a piece of work runs for, carried in ContextVars (``docs/ARCHITECTURE.md`` section 4).

Entry points set it once (gateway auth per request, the job runner per job, the command consumer per command,
the scheduler per tenant, ``farm serve`` for tenant #1) and ``farm.db.pool.tenant_connection`` refuses to hand
out a connection while it is unset. ``asyncio.create_task`` copies the current context, so background tasks
started from a request inherit its tenant; code that outlives the request must set its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

current_tenant: ContextVar[UUID | None] = ContextVar("farm_current_tenant", default=None)
"""Tenant of the work running now, or None when no entry point has set one."""

current_user: ContextVar[UUID | None] = ContextVar("farm_current_user", default=None)
"""User behind the work running now (an API token bound to a user), or None."""


class TenantContextMissing(RuntimeError):
    """A tenant-scoped database checkout was requested while no tenant is set.

    Fail closed: a connection without a tenant would either see every tenant's rows or write rows that belong
    to none, so none is handed out.
    """

    def __init__(self) -> None:
        super().__init__(
            "no tenant is set for this work: an entry point must wrap it in farm.tenancy.use_tenant(...) "
            "(maintenance code that really spans tenants uses farm.db.pool.system_connection)"
        )


def require_tenant() -> UUID:
    """The current tenant id, or ``TenantContextMissing``."""
    tenant = current_tenant.get()
    if tenant is None:
        raise TenantContextMissing
    return tenant


@contextmanager
def use_tenant(tenant_id: UUID, user_id: UUID | None = None) -> Iterator[None]:
    """Run the block for ``tenant_id`` (and ``user_id``); the previous values return on exit."""
    tenant_token = current_tenant.set(tenant_id)
    user_token = current_user.set(user_id)
    try:
        yield
    finally:
        current_user.reset(user_token)
        current_tenant.reset(tenant_token)


@contextmanager
def without_tenant() -> Iterator[None]:
    """Run the block with no tenant (the HTTP process between requests; tests of the fail-closed checkout)."""
    token = current_tenant.set(None)
    user_token = current_user.set(None)
    try:
        yield
    finally:
        current_user.reset(user_token)
        current_tenant.reset(token)
