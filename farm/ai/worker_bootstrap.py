"""Process-local, hash-only leases for a native job's Farm MCP connection."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from farm.db.pool import DbPool, system_connection
from farm.secrets import WORKER_TOKEN_PREFIX
from farm.tenancy import TokenIdentity

SERVER_NAME = "harness-farm"
TOKEN_ENV = "FARM_WORKER_MCP_TOKEN"
INSPECTION_TOOLS = frozenset({
    "search_tools", "describe_tool", "call_tool", "list_server_tools", "list_mcp_connections",
    "farm_operator", "get_capacity", "list_resources", "get_usage", "worker_profiles",
})
_EXTERNAL_NAME = re.compile(r"(?=.{1,64}\Z)[A-Za-z0-9_-]+__[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class WorkerLaunch:
    url: str
    tools: tuple[str, ...]
    bearer: str = field(repr=False)


current_worker_launch: ContextVar[WorkerLaunch | None] = ContextVar("farm_worker_launch", default=None)


@dataclass(frozen=True)
class _Lease:
    token_id: UUID
    job_id: UUID
    owner_id: UUID
    tenant_id: UUID
    user_id: UUID
    parent_token_id: UUID
    tools: tuple[str, ...]
    expires: float
    heartbeat_s: float


class WorkerBootstrap:
    def __init__(self, pool: DbPool, url: str, external_tools: frozenset[str], max_parallel: int) -> None:
        self.pool = pool
        self.url = url
        self.tools = INSPECTION_TOOLS | external_tools
        self.execution_gate = asyncio.Semaphore(max_parallel)
        self._leases: dict[bytes, _Lease] = {}

    @classmethod
    def configured(
        cls, pool: DbPool, path: str, max_parallel: int, *, port: int | None = None,
    ) -> WorkerBootstrap | None:
        raw_url = os.environ.get("FARM_WORKER_MCP_URL", "").strip()
        if not raw_url:
            return None
        try:
            url = urlsplit(raw_url)
            valid = (
                url.scheme == "http" and url.hostname in {"127.0.0.1", "::1"}
                and url.port is not None and (port is None or url.port == port)
                and url.path == path and bool(re.fullmatch(r"/[A-Za-z0-9_/-]+", path))
                and raw_url == f"http://{url.netloc}{path}"
                and url.username is None and url.password is None
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("FARM_WORKER_MCP_URL must be the Farm's literal loopback HTTP MCP endpoint")
        tools = frozenset(os.environ.get("FARM_WORKER_MCP_TOOLS", "").replace(",", " ").split())
        if any(not _EXTERNAL_NAME.fullmatch(name) for name in tools):
            raise ValueError("FARM_WORKER_MCP_TOOLS requires exact namespace__tool names")
        return cls(pool, raw_url, tools, max_parallel)

    @contextmanager
    def launch(
        self, parent: TokenIdentity | None, *, job_id: UUID, owner_id: UUID,
        tenant_id: UUID, user_id: UUID | None, timeout_s: float, heartbeat_s: float,
    ) -> Iterator[None]:
        from farm.gateway.middleware import tool_scope_allows

        if (parent is None or parent.worker_job_id is not None or user_id is None
                or parent.user_id != user_id or parent.tenant_id != tenant_id):
            yield
            return
        tools = tuple(sorted(name for name in self.tools if tool_scope_allows(parent.scopes, name)))
        if not tools:
            yield
            return
        bearer = WORKER_TOKEN_PREFIX + secrets.token_urlsafe(32)
        digest = hashlib.sha256(bearer.encode()).digest()
        self._leases[digest] = _Lease(
            uuid4(), job_id, owner_id, tenant_id, user_id, parent.token_id, tools,
            time.monotonic() + timeout_s, heartbeat_s,
        )
        token = current_worker_launch.set(WorkerLaunch(self.url, tools, bearer))
        try:
            yield
        finally:
            current_worker_launch.reset(token)
            self._leases.pop(digest, None)

    async def verify(self, bearer: str) -> TokenIdentity | None:
        from farm.gateway.middleware import tool_scope_allows

        digest = hashlib.sha256(bearer.encode()).digest()
        lease = self._leases.get(digest)
        if lease is None or lease.expires <= time.monotonic():
            return None
        async with system_connection(self.pool) as conn:
            result = await conn.execute(
                """
                select p.scopes, u.email
                from public.ai_jobs j
                join public.tenants t on t.id = j.tenant_id and t.status = 'active'
                join public.api_tokens p on p.tenant_id = j.tenant_id and p.user_id = j.operator_user_id
                join public.memberships m on m.tenant_id = p.tenant_id and m.user_id = p.user_id
                join public.users u on u.id = p.user_id
                where j.id = %s and j.tenant_id = %s and j.operator_user_id = %s and j.owner_id = %s
                  and j.state = 'running' and j.cancel_requested_at is null
                  and j.heartbeat_at >= now() - (%s * interval '1 second')
                  and p.id = %s and p.revoked_at is null
                """,
                (lease.job_id, lease.tenant_id, lease.user_id, lease.owner_id,
                 lease.heartbeat_s, lease.parent_token_id),
            )
            row = await result.fetchone()
        if row is None or digest not in self._leases or lease.expires <= time.monotonic():
            return None
        scopes = tuple(f"tool:{name}" for name in lease.tools if tool_scope_allows(tuple(row[0]), name))
        if not scopes:
            return None
        return TokenIdentity(
            token_id=lease.token_id, tenant_id=lease.tenant_id, user_id=lease.user_id,
            name=f"worker:{lease.job_id}", scopes=scopes, user_email=row[1], worker_job_id=lease.job_id,
        )
