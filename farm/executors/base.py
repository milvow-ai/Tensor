"""The executor contract every resource implements (docs: briefs/CONTEXT.md section 4).

Executors never raise for provider errors: they classify them into :class:`ErrorKind` and return an
:class:`ExecResult`. Only programming errors raise.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field


class ErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    AUTH = "auth"
    EMPTY = "empty"
    TIMEOUT = "timeout"
    SERVER = "server"
    BAD_REQUEST = "bad_request"
    LIMIT_REACHED = "limit_reached"
    NEEDS_LOGIN = "needs_login"
    UNKNOWN = "unknown"


class ConnectionView(BaseModel):
    """What an executor may see of a connection (never a secret value, only the ``auth_ref``)."""

    id: str
    provider_id: str
    auth_ref: str
    meta: dict[str, Any] = Field(default_factory=dict)
    concurrency: int = 1
    rate_per_min: int | None = None


class ExecRequest(BaseModel):
    request_id: UUID
    capability: str
    params: dict[str, Any]
    connection: ConnectionView
    timeout_s: float = 30


class ExecResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    found: bool | None = None
    units_used: dict[str, float] = Field(default_factory=dict)
    cost_usd: Decimal = Decimal(0)
    error_kind: ErrorKind | None = None
    error: str | None = None
    retry_after_s: float | None = None
    reset_at: datetime | None = None
    latency_ms: int = 0


@runtime_checkable
class Executor(Protocol):
    async def execute(self, req: ExecRequest) -> ExecResult: ...
