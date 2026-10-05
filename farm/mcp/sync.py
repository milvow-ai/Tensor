"""``farm mcp sync``: list each MCP server's tools through one of its accounts into ``mcp_tools``.

* the first usable account of a provider (priority order; paused, needs-login, exhausted, cooling-down and
  circuit-open accounts are skipped, exactly as the router skips them) lists the tools, following the server's
  pagination; if it cannot, the next one is tried, and every failure is recorded in that account's health;
* the allow / deny globs of the provider decide what is stored; a tool the server no longer lists, or that is
  now denied, is removed;
* a tool whose definition changed gets a new ``schema_hash``; the change is logged (``mcp.schema_changed``);
* a server that cannot be reached leaves the stored catalogue as it was and the provider's accounts marked
  unhealthy; other providers are not affected (they are synced concurrently and independently).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from farm.db.pool import DbPool
from farm.executors.base import ErrorKind
from farm.executors.mcp.client import McpExecutor
from farm.executors.mcp.connect import McpFailure
from farm.mcp import store
from farm.mcp.expose import is_allowed
from farm.resources import health
from farm.secrets import redact

log = structlog.get_logger(__name__)

LIST_TIMEOUT_S = 60.0
"""A stdio server started through ``npx`` may need a while the first time."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SyncResult:
    provider: str
    ok: bool
    connection: str | None = None
    """The account that listed the tools."""
    tools: tuple[str, ...] = ()
    """What is stored now (allowed tools)."""
    denied: tuple[str, ...] = ()
    diff: store.SyncDiff | None = None
    error: str | None = None
    """Why no account could list the tools (kind and message of each attempt; never a secret)."""


async def sync_provider(
    pool: DbPool,
    executor: McpExecutor,
    provider: store.McpProvider,
    *,
    clock: Callable[[], datetime] = _utc_now,
    timeout_s: float = LIST_TIMEOUT_S,
) -> SyncResult:
    """Sync one provider. Never raises for a server that misbehaves: that is ``ok=False`` with the reasons."""
    await store.ensure_capability(pool, provider)
    now = clock()
    attempts: list[str] = []
    for account in await store.list_accounts(pool, provider.id):
        blocked = health.block_reason(account.status, account.circuit, account.cooldown_until, now)
        if blocked is not None:
            attempts.append(f"{account.view.id}: skipped ({blocked})")
            continue
        try:
            listed = await executor.list_tools(account.view, timeout_s=timeout_s)
        except McpFailure as failure:
            attempts.append(f"{account.view.id}: {failure.kind.value} ({failure.message})")
            log.warning(
                "mcp.sync_failed",
                provider=provider.id,
                connection=account.view.id,
                kind=failure.kind.value,
                error=redact(failure.message),
            )
            await health.record_failure(
                pool,
                account.view.id,
                failure.kind,
                failure.message,
                now=now,
                retry_after_s=failure.retry_after_s,
            )
            continue
        await health.record_success(pool, account.view.id, now=now)
        kept = [tool for tool in listed if is_allowed(tool.name, provider.spec.tools)]
        diff = await store.replace_tools(pool, provider.id, kept, now)
        for name, old_hash, new_hash in diff.changed:
            log.info(
                "mcp.schema_changed", provider=provider.id, tool=name, old_hash=old_hash, new_hash=new_hash
            )
        denied = tuple(sorted(tool.name for tool in listed if not is_allowed(tool.name, provider.spec.tools)))
        log.info(
            "mcp.sync_done",
            provider=provider.id,
            connection=account.view.id,
            tools=len(kept),
            added=len(diff.added),
            changed=len(diff.changed),
            removed=len(diff.removed),
            denied=len(denied),
        )
        return SyncResult(
            provider.id,
            ok=True,
            connection=account.view.id,
            tools=tuple(sorted(tool.name for tool in kept)),
            denied=denied,
            diff=diff,
        )
    reason = "; ".join(attempts) or "the provider has no accounts"
    return SyncResult(provider.id, ok=False, error=reason)


async def sync_all(
    pool: DbPool,
    executor: McpExecutor,
    *,
    only: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
    timeout_s: float = LIST_TIMEOUT_S,
) -> list[SyncResult]:
    """Sync every enabled pass-through provider (or just ``only``), concurrently and independently."""
    providers = await store.list_providers(pool, provider_id=only)

    async def one(provider: store.McpProvider) -> SyncResult:
        try:
            return await sync_provider(pool, executor, provider, clock=clock, timeout_s=timeout_s)
        except Exception as exc:  # a bug in one provider's sync must not take the others down
            log.error("mcp.sync_crashed", provider=provider.id, error=type(exc).__name__)
            return SyncResult(provider.id, ok=False, error=f"{ErrorKind.UNKNOWN.value}: {type(exc).__name__}")

    return list(await asyncio.gather(*(one(provider) for provider in providers)))
