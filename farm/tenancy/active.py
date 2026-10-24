"""Run per-tenant work for every active tenant (the scheduler's loop, the MCP catalogue refresh)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import structlog

from farm.db.pool import DbPool, system_connection
from farm.tenancy.context import use_tenant

log = structlog.get_logger(__name__)


async def active_tenant_ids(pool: DbPool) -> list[UUID]:
    """Ids of the tenants whose work runs now (read as the platform role)."""
    async with system_connection(pool) as conn:
        cur = await conn.execute(
            "select id from public.tenants where status = 'active' order by created_at, id"
        )
        return [row[0] for row in await cur.fetchall()]


async def for_each_tenant[T](pool: DbPool, work: Callable[[], Awaitable[T]], *, task: str) -> list[T]:
    """Run ``work`` once per active tenant, each inside that tenant's context.

    One tenant's failure is logged and skipped: it must not stop maintenance for the others.
    """
    results: list[T] = []
    for tenant_id in await active_tenant_ids(pool):
        with use_tenant(tenant_id):
            try:
                results.append(await work())
            except Exception as exc:
                log.error("tenant_task.failed", task=task, tenant=str(tenant_id), error=type(exc).__name__)
    return results
