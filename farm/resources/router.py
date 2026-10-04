"""The router: one capability call, from request to envelope (HANDOFF 4.1, 4.3, 4.8).

``route(ctx, capability, params, caller=...)`` is generic: nothing in here knows a capability by name. What a
capability accepts comes from ``farm.capabilities.schemas.CAPABILITY_MODELS``, which providers serve it and in
which order from the ``capability_routes`` table, how an account is charged from ``consumption_units``, and
whether an "empty" answer moves on to the next pool from ``farm.capabilities.policy``.

Flow, every step written to ``run_events`` with a reason::

    validate -> request_hash -> cache -> single-flight -> [per pool, route order]
        eligibility (skip) -> strategy order -> policy -> reserve -> execute -> commit | release
        -> health -> next candidate on failure ... -> envelope

* **cache**: a succeeded ``capability_requests`` row that has not expired answers the call (``cache_hit``,
  cost 0). Answers with ``found = False`` (inconclusive or empty) are never cached.
* **single-flight**: concurrent identical requests collapse into one execution. In-process, callers join the
  running task (``single_flight_join``); across processes the unique ``capability_requests`` row is the lock
  (see ``farm.resources.flight``). The execution runs as its own task, so a caller that goes away (client
  disconnect, cancellation) cannot abandon a provider call half way: it still settles its reservations.
* **reserve first**: every unit of a candidate with ``estimate_per_call > 0`` is reserved atomically before
  the provider is called (all or nothing). A candidate that cannot reserve is skipped (``reserve_failed``),
  so an exhausted account is never called.
* **settle**: after the call each reservation is committed with the *actual* amount or released. What the
  executor reports in ``units_used`` is the truth, for failed calls as well (an LLM call that burnt tokens
  and then failed still costs them). Where it reports nothing, ``charged_on`` decides (``charge_for``). A
  reservation whose process dies expires by TTL.
* **fallback**: any failure moves on to the next candidate (same pool by strategy, then the next pool). The
  health module learns from every outcome. An "empty" answer (``ok``, ``found=False``, ``EMPTY``) is a
  completed call: it is charged as reported, does not count against the account, and moves on only if the
  capability says so (``falls_back_on_empty``); if every pool is empty the empty answer is the result. When
  nothing answers the envelope is ``ok = false`` with the last executed failure's kind and a summary of every
  candidate that was considered.
* **pin**: ``pin=<connection id>`` runs on exactly that connection (its eligibility rules still apply),
  without cache or single-flight, because the caller asked for that account.

Cost of a call = ``actual units x unit_cost_usd`` of what was committed, plus the executor's own ``cost_usd``
(pay-as-you-go executors). Configure the price in one of the two places for a given resource, not both.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import traceback
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
import structlog
from pydantic import BaseModel, ValidationError

from farm.capabilities.policy import falls_back_on_empty
from farm.capabilities.schemas import CAPABILITY_MODELS
from farm.context import FarmContext
from farm.db.pool import DbPool
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult, Executor
from farm.resources import flight, health, ledger
from farm.resources.strategies import ResolvedStrategy, order_candidates, resolve_strategy
from farm.resources.trajectory import Trajectory
from farm.secrets import redact

log = structlog.get_logger(__name__)

type Models = tuple[type[BaseModel], type[BaseModel]]

TIMEOUT_GRACE_S = 5.0
"""Router-side allowance on top of an executor's own ``timeout_s`` before the router cuts the call off."""
RESERVATION_MARGIN_S = 60.0
LEASE_MARGIN_S = 30.0
MAX_MESSAGE_CHARS = 300

NO_CAPACITY = "no_capacity"
POLICY_BLOCKED = "policy_blocked"
INTERNAL = "internal"

HINTS: dict[str, str] = {
    ErrorKind.RATE_LIMITED: (
        "The provider asked us to slow down; the account cools down by itself. "
        "Retry later, or add another account to the pool."
    ),
    ErrorKind.AUTH: (
        "The provider rejected the credentials. Check the key (auth_ref) of the connection in .env "
        "or the Console, then set the account active again."
    ),
    ErrorKind.LIMIT_REACHED: (
        "The account's quota is used up. It comes back at its reset, or add credits / another account."
    ),
    ErrorKind.TIMEOUT: (
        "The provider did not answer in time. Retry; if it repeats, raise timeout_s on the connection "
        "(meta) or the provider (config)."
    ),
    ErrorKind.SERVER: (
        "The provider is failing (5xx). Other accounts and pools were tried; "
        "check the provider's status page and retry later."
    ),
    ErrorKind.BAD_REQUEST: "The request was rejected as invalid; check the input for this capability.",
    ErrorKind.EMPTY: "The provider found nothing for this input.",
    ErrorKind.NEEDS_LOGIN: (
        "The account needs a login: log it in again (Console > Accounts), then set it active."
    ),
    ErrorKind.UNKNOWN: (
        "The provider answered in a way the Farm did not understand; "
        "see the run trajectory (get_run) for the details."
    ),
    NO_CAPACITY: (
        "No connection could take the call (every one was skipped or out of quota). "
        "The attempts list says why; get_capacity shows when each account is back."
    ),
    POLICY_BLOCKED: (
        "A budget of 0 blocks every paid connection of this capability. "
        "Raise the budget in the Console (or registry), or add a free account."
    ),
    INTERNAL: (
        "The Farm hit an unexpected error; the run trajectory (get_run) and the Farm log have the details."
    ),
}


class UnknownCapability(LookupError):
    """The capability is not in the ``capabilities`` table (run ``farm registry sync``)."""


# --- the envelope (briefs/CONTEXT.md section 4) -----------------------------------------------------


class AttemptSummary(BaseModel):
    provider: str
    connection_id: str | None
    outcome: Literal["skipped", "policy_blocked", "reserve_failed", "empty", "failed"]
    kind: str
    message: str


class RouteError(BaseModel):
    kind: str
    message: str
    hint: str
    retry_after_s: float | None = None
    attempts: list[AttemptSummary] = []


class Source(BaseModel):
    provider: str
    connection_id: str
    cached: bool


class Cost(BaseModel):
    usd: float = 0.0
    units: dict[str, float] = {}


class RouteOutcome(BaseModel):
    ok: bool
    result: dict[str, Any] | None = None
    error: RouteError | None = None
    run_id: UUID
    source: Source | None = None
    cost: Cost = Cost()

    def envelope(self) -> dict[str, Any]:
        """JSON-safe dict: the exact payload the MCP tools return."""
        return self.model_dump(mode="json")


# --- loading (the database is the runtime source of truth) ------------------------------------------


@dataclass(frozen=True)
class CapabilityInfo:
    name: str
    default_strategy: str | None
    cache_ttl_seconds: int


@dataclass(frozen=True)
class ProviderPool:
    id: str
    name: str
    executor: str
    enabled: bool
    default_strategy: str | None
    config: Mapping[str, Any]


@dataclass(frozen=True)
class UnitConfig:
    unit: str
    limit: Decimal | None
    period: str
    charged_on: str
    unit_cost_usd: Decimal
    estimate: Decimal


@dataclass(frozen=True)
class Candidate:
    """One connection as the router sees it (``id`` and ``priority`` make it rankable by a strategy)."""

    id: str
    provider_id: str
    auth_ref: str
    scope: tuple[str, ...]
    priority: int
    status: str
    meta: Mapping[str, Any]
    concurrency: int
    rate_per_min: int | None
    circuit: str
    cooldown_until: datetime | None
    units: tuple[UnitConfig, ...]


@dataclass(frozen=True)
class Budget:
    monthly_usd: Decimal
    hard_stop: bool


type Budgets = Mapping[tuple[str, str | None], Budget]


async def _load_capability(pool: DbPool, name: str) -> CapabilityInfo | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select default_strategy, cache_ttl_seconds from public.capabilities where name = %s", (name,)
        )
        row = await cur.fetchone()
    return None if row is None else CapabilityInfo(name, row[0], row[1])


async def _load_routes(pool: DbPool, capability: str) -> list[ProviderPool]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select p.id, p.name, p.executor, p.enabled, p.default_strategy, p.config "
            "from public.capability_routes r join public.providers p on p.id = r.provider_id "
            "where r.capability = %s and r.enabled order by r.position, r.provider_id",
            (capability,),
        )
        rows = await cur.fetchall()
    return [ProviderPool(*row) for row in rows]


async def _load_candidates(pool: DbPool, provider_id: str) -> list[Candidate]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "select cn.id, cn.provider_id, cn.auth_ref, cn.scope, cn.priority, cn.status, cn.meta, "
            "cn.concurrency, cn.rate_per_min, coalesce(h.circuit, 'closed'), h.cooldown_until "
            "from public.connections cn left join public.connection_health h on h.connection_id = cn.id "
            "where cn.provider_id = %s order by cn.priority, cn.id",
            (provider_id,),
        )
        connections = await cur.fetchall()
        cur = await conn.execute(
            "select connection_id, unit, limit_value, period, charged_on, unit_cost_usd, estimate_per_call "
            "from public.consumption_units where connection_id = any(%s) order by connection_id, unit",
            ([c[0] for c in connections],),
        )
        unit_rows = await cur.fetchall()
    units: dict[str, list[UnitConfig]] = {}
    for u in unit_rows:
        units.setdefault(u[0], []).append(UnitConfig(*u[1:]))
    return [
        Candidate(
            id=c[0],
            provider_id=c[1],
            auth_ref=c[2] or "",
            scope=tuple(c[3]),
            priority=c[4],
            status=c[5],
            meta=c[6],
            concurrency=c[7],
            rate_per_min=c[8],
            circuit=c[9],
            cooldown_until=c[10],
            units=tuple(units.get(c[0], ())),
        )
        for c in connections
    ]


async def _load_budgets(pool: DbPool) -> Budgets:
    async with pool.connection() as conn:
        cur = await conn.execute("select scope, ref, monthly_usd, hard_stop from public.budgets")
        rows = await cur.fetchall()
    return {(r[0], r[1]): Budget(r[2], r[3]) for r in rows}


async def _provider_of(pool: DbPool, connection_id: str) -> str | None:
    async with pool.connection() as conn:
        cur = await conn.execute("select provider_id from public.connections where id = %s", (connection_id,))
        row = await cur.fetchone()
    return None if row is None else str(row[0])


# --- pure rules (unit-tested in tests/test_router.py) -----------------------------------------------


def request_hash(capability: str, params: Mapping[str, Any]) -> str:
    """Identity of a request (CONTEXT section 4): equal capability + equal params = the same request."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(f"{capability}|{canonical}".encode()).hexdigest()


def skip_reason(candidate: Candidate, scope: str, now: datetime) -> str | None:
    """Why a connection is not tried at all (``skip`` event), or ``None``."""
    blocked = health.block_reason(candidate.status, candidate.circuit, candidate.cooldown_until, now)
    if blocked is not None:
        return blocked
    if scope not in candidate.scope:
        return "scope"
    return None


def paid_spend_block(candidate: Candidate, budgets: Budgets) -> str | None:
    """M1 policy: a connection that costs money is blocked while an applicable budget is 0 (hard stop).

    Applicable = the connection's own, its provider's and the global budget; any one of them at 0 blocks.
    Free connections (no unit with a price) are never blocked. Spend-versus-cap enforcement is M3b.
    """
    if not any(u.unit_cost_usd > 0 and u.estimate > 0 for u in candidate.units):
        return None
    for scope, ref in (("connection", candidate.id), ("provider", candidate.provider_id), ("global", None)):
        budget = budgets.get((scope, ref))
        if budget is not None and budget.hard_stop and budget.monthly_usd <= 0:
            return f"{scope} budget{'' if ref is None else f' ({ref})'} is 0 and the connection is paid"
    return None


def charge_for(unit: UnitConfig, result: ExecResult) -> tuple[Decimal, str]:
    """What one finished call consumed of ``unit``, and why (the ``reason`` of the commit/release event).

    The executor knows what the provider charged, so its ``units_used`` is the truth whatever the outcome:
    a call that failed after spending tokens, or an empty answer the provider still billed, is committed as
    reported. Where the executor reports nothing for the unit, ``charged_on`` decides:

    * ``attempt``: the attempt itself is the charge, so the estimate;
    * ``success`` / ``found``: nothing (a provider that refunds inconclusive results, like Reoon and
      ZeroBounce on ``unknown``, reports no usage for them).
    Executors that charge a ``success`` / ``found`` unit must therefore report it in ``units_used``.
    """
    reported_raw = result.units_used.get(unit.unit)
    if reported_raw is not None:
        return Decimal(str(reported_raw)), "usage reported by the executor"
    if unit.charged_on == "attempt":
        return unit.estimate, "charged on attempt: the estimate (the executor reported no usage)"
    return Decimal(0), f"the executor reported no usage; unit is charged on {unit.charged_on}"


def _timeout_for(candidate: Candidate, pool: ProviderPool, default_s: float) -> float:
    for source in (candidate.meta, pool.config):
        value = source.get("timeout_s")
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return float(value)
    return default_s


def _log_crash(event: str, exc: BaseException, **fields: Any) -> None:
    """Log a bug with its traceback, redacted: an exception message can carry a URL with a key in it."""
    trace = redact("".join(traceback.format_exception(exc)))
    log.error(event, error=f"{type(exc).__name__}: {redact(str(exc))}", traceback=trace[-4000:], **fields)


def _clip(text: str | None) -> str:
    flat = " ".join(redact(text or "").split())
    return flat if len(flat) <= MAX_MESSAGE_CHARS else flat[: MAX_MESSAGE_CHARS - 1] + "…"


# --- outcome of one execution (shared by every caller of a single-flight) ---------------------------


@dataclass(frozen=True)
class _Settled:
    ok: bool
    status: Literal["succeeded", "failed", "blocked"]
    data: dict[str, Any] | None = None
    found: bool | None = None
    provider: str | None = None
    connection_id: str | None = None
    cached: bool = False
    cost_usd: Decimal = Decimal(0)
    units: dict[str, float] = field(default_factory=dict)
    error: RouteError | None = None

    def free(self) -> _Settled:
        """The same outcome as seen by a caller that did not pay for it (cache hit, single-flight join)."""
        return replace(self, cost_usd=Decimal(0), units={})


def _settled_from_stored(stored: flight.StoredResult, *, cached: bool) -> _Settled:
    return _Settled(
        ok=True,
        status="succeeded",
        data=stored.data,
        found=stored.found,
        provider=stored.provider,
        connection_id=stored.connection_id,
        cached=cached,
    )


def _settled_error(error: RouteError) -> _Settled:
    return _Settled(ok=False, status="blocked" if error.kind == POLICY_BLOCKED else "failed", error=error)


def _error(kind: str, message: str, attempts: list[AttemptSummary] | None = None, **extra: Any) -> RouteError:
    return RouteError(
        kind=kind, message=_clip(message), hint=HINTS.get(kind, ""), attempts=attempts or [], **extra
    )


def _outcome(run_id: UUID, settled: _Settled) -> RouteOutcome:
    source = (
        Source(provider=settled.provider, connection_id=settled.connection_id, cached=settled.cached)
        if settled.provider is not None and settled.connection_id is not None
        else None
    )
    return RouteOutcome(
        ok=settled.ok,
        result=settled.data if settled.ok else None,
        error=settled.error,
        run_id=run_id,
        source=source,
        cost=Cost(usd=float(settled.cost_usd), units=dict(settled.units)),
    )


@dataclass(frozen=True)
class _Call:
    capability: str
    cap: CapabilityInfo
    params: dict[str, Any]
    request_hash: str
    caller: str
    strategy: str | None
    pin: str | None
    scope: str
    models: Models | None


@dataclass(frozen=True)
class _Held:
    unit: UnitConfig
    reservation_id: UUID


@dataclass
class _Spent:
    cost: Decimal = field(default_factory=lambda: Decimal(0))
    units: dict[str, Decimal] = field(default_factory=dict)


def _failed(kind: ErrorKind, message: str, *, latency_ms: int = 0) -> ExecResult:
    return ExecResult(ok=False, error_kind=kind, error=message, latency_ms=latency_ms)


def _checked(result: ExecResult, models: Models | None) -> ExecResult:
    """A successful answer must carry data that matches the capability's output model, or it is a failure."""
    if result.data is None:
        return result.model_copy(
            update={"ok": False, "error_kind": ErrorKind.UNKNOWN, "error": "the executor returned no data"}
        )
    if models is None:
        return result
    try:
        models[1].model_validate(result.data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False)
        )
        return result.model_copy(
            update={
                "ok": False,
                "data": None,
                "error_kind": ErrorKind.UNKNOWN,
                "error": f"the provider's answer does not match {models[1].__name__}: {problems}",
            }
        )
    return result


async def _best_effort[T](work: Awaitable[T], what: str) -> bool:
    """Bookkeeping must never throw away an answer the provider already charged for: log and go on."""
    try:
        await work
    except psycopg.Error as exc:
        log.error("router.bookkeeping_failed", step=what, error=type(exc).__name__)
        return False
    return True


# --- the execution of one request: pools, candidates, attempts --------------------------------------


class _Chain:
    """Runs the candidates of one request until one answers. Its state is what the envelope summarises."""

    def __init__(self, ctx: FarmContext, call: _Call, traj: Trajectory, request_id: UUID) -> None:
        self.ctx = ctx
        self.call = call
        self.traj = traj
        self.request_id = request_id
        self.attempts: list[AttemptSummary] = []
        self.cost_usd = Decimal(0)
        self.units: dict[str, float] = {}
        self.last_failure: ExecResult | None = None
        self.last_provider: str | None = None
        self.last_connection: str | None = None
        self.empty_answer: _Settled | None = None
        self.failed_from: str | None = None
        self.fallback_reason: str | None = None
        self.policy_blocks = 0

    def settled_failure(self, error: RouteError) -> _Settled:
        """A failed outcome that still reports what the failed attempts cost and where the last one went."""
        return replace(
            _settled_error(error),
            provider=self.last_provider,
            connection_id=self.last_connection,
            cost_usd=self.cost_usd,
            units=dict(self.units),
        )

    async def run(self) -> _Settled:
        call = self.call
        routes = await _load_routes(self.ctx.pool, call.capability)
        if call.pin is not None:
            restricted = await self._restrict_to_pin(routes, call.pin)
            if isinstance(restricted, _Settled):
                return restricted
            routes = restricted
        budgets = await _load_budgets(self.ctx.pool)

        await self.traj.event(
            "plan",
            caller=call.caller,
            request_hash=call.request_hash,
            requested_strategy=call.strategy,
            pin=call.pin,
            scope=call.scope,
            cache_ttl_seconds=call.cap.cache_ttl_seconds,
            routes=[p.id for p in routes],
        )
        for provider_pool in routes:
            settled = await self._try_pool(provider_pool, budgets)
            if settled is not None:
                return settled
        return self._exhausted()

    async def _restrict_to_pin(self, routes: list[ProviderPool], pin: str) -> list[ProviderPool] | _Settled:
        provider_id = await _provider_of(self.ctx.pool, pin)
        if provider_id is None:
            return await self._reject(f"pin: there is no connection '{pin}'")
        pinned = [p for p in routes if p.id == provider_id]
        if not pinned:
            return await self._reject(
                f"pin: connection '{pin}' (provider '{provider_id}') is not on the route of "
                f"'{self.call.capability}'"
            )
        return pinned

    async def _reject(self, message: str) -> _Settled:
        await self.traj.event("failure", error_kind=ErrorKind.BAD_REQUEST, error=message)
        return _settled_error(_error(ErrorKind.BAD_REQUEST, message))

    async def _try_pool(self, provider_pool: ProviderPool, budgets: Budgets) -> _Settled | None:
        ctx, call = self.ctx, self.call
        if not provider_pool.enabled:
            await self._skip(provider_pool, None, "provider_disabled")
            return None
        executor = ctx.executors.get(provider_pool.executor)
        if executor is None:
            await self._skip(provider_pool, None, "no_executor", executor=provider_pool.executor)
            return None

        candidates = await _load_candidates(ctx.pool, provider_pool.id)
        if call.pin is not None:
            candidates = [c for c in candidates if c.id == call.pin]
        if not candidates:
            await self._skip(provider_pool, None, "no_connections")
            return None
        now = ctx.clock()
        eligible: list[Candidate] = []
        for candidate in candidates:
            reason = skip_reason(candidate, call.scope, now)
            if reason is not None:
                await self._skip(provider_pool, candidate, reason, cooldown_until=candidate.cooldown_until)
            else:
                eligible.append(candidate)

        resolved = resolve_strategy(
            requested=call.strategy,
            pin=call.pin,
            capability=call.cap.default_strategy,
            provider=provider_pool.default_strategy,
        )
        for candidate in order_candidates(eligible, resolved.name, call.pin):
            settled = await self._try_connection(provider_pool, candidate, executor, resolved, budgets)
            if settled is not None:
                return settled
        return None

    async def _skip(
        self, provider_pool: ProviderPool, candidate: Candidate | None, reason: str, **detail: Any
    ) -> None:
        connection_id = None if candidate is None else candidate.id
        await self.traj.event("skip", connection_id, provider=provider_pool.id, reason=reason, **detail)
        self.attempts.append(
            AttemptSummary(
                provider=provider_pool.id,
                connection_id=connection_id,
                outcome="skipped",
                kind=reason,
                message=f"skipped: {reason.replace('_', ' ')}",
            )
        )

    async def _try_connection(
        self,
        provider_pool: ProviderPool,
        candidate: Candidate,
        executor: Executor,
        resolved: ResolvedStrategy,
        budgets: Budgets,
    ) -> _Settled | None:
        ctx, call, traj = self.ctx, self.call, self.traj
        if self.failed_from is not None:
            await traj.event(
                "fallback", self.failed_from, to_connection=candidate.id, reason=self.fallback_reason
            )
            self.failed_from = None

        blocked = paid_spend_block(candidate, budgets)
        if blocked is not None:
            self.policy_blocks += 1
            await traj.event("policy_block", candidate.id, provider=provider_pool.id, reason=blocked)
            self.attempts.append(
                AttemptSummary(
                    provider=provider_pool.id,
                    connection_id=candidate.id,
                    outcome="policy_blocked",
                    kind=POLICY_BLOCKED,
                    message=blocked,
                )
            )
            return None

        await traj.event(
            "candidate",
            candidate.id,
            provider=provider_pool.id,
            strategy=resolved.name,
            strategy_note=resolved.note,
            priority=candidate.priority,
        )
        held = await self._reserve(provider_pool, candidate)
        if held is None:
            return None

        timeout_s = _timeout_for(candidate, provider_pool, ctx.default_timeout_s)
        request = ExecRequest(
            request_id=self.request_id,
            capability=call.capability,
            params=call.params,
            connection=ConnectionView(
                id=candidate.id,
                provider_id=candidate.provider_id,
                auth_ref=candidate.auth_ref,
                meta=dict(candidate.meta),
                concurrency=candidate.concurrency,
                rate_per_min=candidate.rate_per_min,
            ),
            timeout_s=timeout_s,
        )
        await traj.event("execute", candidate.id, provider=provider_pool.id, timeout_s=timeout_s)
        self.last_provider, self.last_connection = provider_pool.id, candidate.id
        try:
            result = await self._execute(executor, request, timeout_s)
        except BaseException:
            # Cancelled (deadline, shutdown): the provider may or may not have been reached, so settle like a
            # failed attempt before letting the cancellation go.
            await asyncio.shield(self._settle(candidate, held, _failed(ErrorKind.UNKNOWN, "interrupted")))
            raise

        if result.ok:
            result = _checked(result, call.models)
        # Shielded: a deadline or shutdown while the books are written must not leave a reservation open.
        spent = await asyncio.shield(self._settle(candidate, held, result))
        self.cost_usd += spent.cost + result.cost_usd
        for unit, amount in spent.units.items():
            self.units[unit] = self.units.get(unit, 0.0) + float(amount)

        if result.ok:
            return await self._answered(provider_pool, candidate, result)
        await self._on_failure(provider_pool, candidate, result)
        return None

    async def _answered(
        self, provider_pool: ProviderPool, candidate: Candidate, result: ExecResult
    ) -> _Settled | None:
        """The provider answered. An empty answer is a completed call that the account is not blamed for."""
        ctx, call = self.ctx, self.call
        empty = result.error_kind is ErrorKind.EMPTY
        if not empty:
            await _best_effort(
                health.record_success(ctx.pool, candidate.id, now=ctx.clock()), "health.success"
            )
        await self.traj.event(
            "success",
            candidate.id,
            provider=provider_pool.id,
            latency_ms=result.latency_ms,
            found=result.found,
            empty=empty,
            units_used=result.units_used,
            cost_usd=self.cost_usd,
        )
        settled = _Settled(
            ok=True,
            status="succeeded",
            data=result.data,
            found=result.found,
            provider=provider_pool.id,
            connection_id=candidate.id,
            cost_usd=self.cost_usd,
            units=dict(self.units),
        )
        if empty and falls_back_on_empty(call.capability):
            self.empty_answer = settled
            self.failed_from, self.fallback_reason = candidate.id, "empty"
            self.attempts.append(
                AttemptSummary(
                    provider=provider_pool.id,
                    connection_id=candidate.id,
                    outcome="empty",
                    kind=ErrorKind.EMPTY.value,
                    message=_clip(result.error) or "the provider found nothing",
                )
            )
            return None
        return settled

    async def _on_failure(
        self, provider_pool: ProviderPool, candidate: Candidate, result: ExecResult
    ) -> None:
        ctx = self.ctx
        kind = result.error_kind or ErrorKind.UNKNOWN
        await _best_effort(
            health.record_failure(
                ctx.pool,
                candidate.id,
                kind,
                result.error,
                now=ctx.clock(),
                retry_after_s=result.retry_after_s,
                reset_at=result.reset_at,
            ),
            "health.failure",
        )
        await self.traj.event(
            "failure",
            candidate.id,
            provider=provider_pool.id,
            error_kind=kind,
            error=result.error,
            retry_after_s=result.retry_after_s,
            latency_ms=result.latency_ms,
        )
        self.last_failure = result
        self.failed_from, self.fallback_reason = candidate.id, kind.value
        self.attempts.append(
            AttemptSummary(
                provider=provider_pool.id,
                connection_id=candidate.id,
                outcome="failed",
                kind=kind.value,
                message=_clip(result.error),
            )
        )

    async def _reserve(self, provider_pool: ProviderPool, candidate: Candidate) -> list[_Held] | None:
        """Reserve every unit with an estimate, all or nothing. ``None`` = skipped (``reserve_failed``)."""
        ttl_s = int(_timeout_for(candidate, provider_pool, self.ctx.default_timeout_s) + RESERVATION_MARGIN_S)
        held: list[_Held] = []
        try:
            for unit in candidate.units:
                if unit.estimate <= 0:
                    continue
                reservation = await ledger.reserve(
                    self.ctx.pool, candidate.id, unit.unit, unit.estimate, self.request_id, ttl_s
                )
                if reservation is None:
                    await self.traj.event(
                        "reserve_failed",
                        candidate.id,
                        unit=unit.unit,
                        amount=unit.estimate,
                        reason="the unit's remaining quota is below the estimate",
                    )
                    self.attempts.append(
                        AttemptSummary(
                            provider=provider_pool.id,
                            connection_id=candidate.id,
                            outcome="reserve_failed",
                            kind="limit_reached",
                            message=f"not enough '{unit.unit}' left for one call",
                        )
                    )
                    await self._release(
                        candidate, held, "another unit of the connection could not be reserved"
                    )
                    return None
                held.append(_Held(unit, reservation))
                await self.traj.event(
                    "reserve", candidate.id, unit=unit.unit, amount=unit.estimate, reservation_id=reservation
                )
        except BaseException:
            await asyncio.shield(self._release(candidate, held, "interrupted while reserving"))
            raise
        return held

    async def _release(self, candidate: Candidate, held: list[_Held], reason: str) -> None:
        for h in held:
            await _best_effort(ledger.release(self.ctx.pool, h.reservation_id), "ledger.release")
            await self.traj.event("release", candidate.id, unit=h.unit.unit, reason=reason)
        held.clear()

    async def _execute(self, executor: Executor, request: ExecRequest, timeout_s: float) -> ExecResult:
        started = time.monotonic()
        limit = asyncio.timeout(timeout_s + TIMEOUT_GRACE_S)
        try:
            async with limit:
                result = await executor.execute(request)
        except TimeoutError:
            if not limit.expired():
                raise
            return _failed(
                ErrorKind.TIMEOUT,
                f"no answer within {timeout_s:g}s (the router cut the call off)",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            # Executors must not raise for provider faults; if one does it is a bug. Treat it as a failed
            # attempt (so a fallback can still answer) and make the bug loud in the log.
            _log_crash(
                "router.executor_crashed",
                exc,
                connection=request.connection.id,
                capability=request.capability,
            )
            return _failed(ErrorKind.UNKNOWN, f"executor crashed: {type(exc).__name__}: {exc}")
        if result.latency_ms == 0:
            result = result.model_copy(update={"latency_ms": int((time.monotonic() - started) * 1000)})
        return result

    async def _settle(self, candidate: Candidate, held: list[_Held], result: ExecResult) -> _Spent:
        """Commit or release every reservation of one finished attempt; write usage events."""
        spent = _Spent()
        pool = self.ctx.pool
        for h in held:
            actual, reason = charge_for(h.unit, result)
            if actual > 0:
                cost = actual * h.unit.unit_cost_usd
                if await _best_effort(ledger.commit(pool, h.reservation_id, actual), "ledger.commit"):
                    await _best_effort(self._usage_event(candidate, h.unit, actual, cost), "usage_event")
                spent.cost += cost
                spent.units[h.unit.unit] = spent.units.get(h.unit.unit, Decimal(0)) + actual
                await self.traj.event(
                    "commit",
                    candidate.id,
                    unit=h.unit.unit,
                    reserved=h.unit.estimate,
                    actual=actual,
                    cost_usd=cost,
                    reason=reason,
                )
            else:
                await _best_effort(ledger.release(pool, h.reservation_id), "ledger.release")
                await self.traj.event("release", candidate.id, unit=h.unit.unit, reason=reason)
        held.clear()
        return spent

    async def _usage_event(
        self, candidate: Candidate, unit: UnitConfig, actual: Decimal, cost: Decimal
    ) -> None:
        async with self.ctx.pool.connection() as conn:
            await conn.execute(
                "insert into public.usage_events "
                "(connection_id, unit, amount, kind, cost_usd, request_id, run_id, at) "
                "values (%s, %s, %s, 'actual', %s, %s, %s, %s)",
                (candidate.id, unit.unit, actual, cost, self.request_id, self.traj.run_id, self.ctx.clock()),
            )

    def _exhausted(self) -> _Settled:
        """No candidate produced a final answer."""
        if self.last_failure is None and self.empty_answer is not None:
            # Every pool that could be asked said "nothing": that is the answer.
            return replace(self.empty_answer, cost_usd=self.cost_usd, units=dict(self.units))
        last = self.last_failure
        if last is not None:
            error = _error(
                (last.error_kind or ErrorKind.UNKNOWN).value,
                last.error or "the provider failed",
                self.attempts,
                retry_after_s=last.retry_after_s,
            )
        elif self.policy_blocks:
            error = _error(
                POLICY_BLOCKED,
                f"{self.policy_blocks} connection(s) blocked by a zero budget; none could be tried",
                self.attempts,
            )
        else:
            error = _error(NO_CAPACITY, "no connection of this capability could be tried", self.attempts)
        return self.settled_failure(error)


# --- public entry point -----------------------------------------------------------------------------


async def route(
    ctx: FarmContext,
    capability: str,
    params: Mapping[str, Any],
    *,
    strategy: str | None = None,
    pin: str | None = None,
    caller: str,
    scope: str = "internal",
) -> RouteOutcome:
    """Serve one capability call and return its envelope (see the module docstring for the flow).

    Raises :class:`UnknownCapability` for a capability that is not in the database. Provider and policy
    failures are *not* exceptions: they come back as ``ok = false`` with the error, the attempts and a hint.
    A bug (any unexpected exception) finalises the run as failed/``internal``, is logged with its traceback
    and also comes back as an ``ok = false`` envelope (kind ``internal``); only cancellation propagates.
    """
    cap = await _load_capability(ctx.pool, capability)
    if cap is None:
        raise UnknownCapability(f"unknown capability '{capability}' (run `farm registry sync`)")
    traj = await Trajectory.start(
        ctx.pool,
        ctx.clock,
        capability=capability,
        caller=caller,
        strategy="pin" if pin is not None else strategy or cap.default_strategy,
    )
    try:
        return await _route(ctx, cap, traj, params, strategy, pin, caller, scope)
    except Exception as exc:
        # A bug, not a provider fault. The run is finalised and the caller still gets an envelope that
        # names the run (so the owner can find the traceback in the log); only cancellation propagates.
        _log_crash("router.internal_error", exc, run_id=str(traj.run_id), capability=capability)
        await _abort(traj, exc)
        return _outcome(traj.run_id, _settled_error(_error(INTERNAL, f"{type(exc).__name__}: {exc}")))
    except BaseException as exc:
        await _abort(traj, exc)
        raise


async def _abort(traj: Trajectory, exc: BaseException, *, owner: bool = False) -> None:
    """An unexpected error: the run must not stay 'running'. Runs handed to another task are left to it."""
    if traj.finished or (traj.delegated and not owner):
        return
    await asyncio.shield(
        traj.finish(
            status="failed",
            cost_usd=Decimal(0),
            cached=False,
            connection_id=None,
            error_kind=INTERNAL,
            error=f"{type(exc).__name__}: {exc}",
        )
    )


async def _route(
    ctx: FarmContext,
    cap: CapabilityInfo,
    traj: Trajectory,
    params: Mapping[str, Any],
    strategy: str | None,
    pin: str | None,
    caller: str,
    scope: str,
) -> RouteOutcome:
    models = CAPABILITY_MODELS.get(cap.name)
    try:
        normalised = (
            models[0].model_validate(dict(params)).model_dump(mode="json") if models else dict(params)
        )
        resolve_strategy(requested=strategy, pin=pin, capability=cap.default_strategy, provider=None)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False)
        )
        return await _reject(traj, f"invalid input for '{cap.name}': {problems}")
    except ValueError as exc:
        return await _reject(traj, str(exc))

    call = _Call(
        capability=cap.name,
        cap=cap,
        params=normalised,
        request_hash=request_hash(cap.name, normalised),
        caller=caller,
        strategy=strategy,
        pin=pin,
        scope=scope,
        models=models,
    )

    if pin is not None:
        return _outcome(traj.run_id, await _lead(ctx, call, traj, shared=False))

    hit = await flight.lookup_cache(ctx.pool, call.request_hash, ctx.clock())
    if hit is not None:
        await _record_cache_hit(traj, call, hit.stored, hit.expires_at, hit.run_id)
        return await _finish(traj, _settled_from_stored(hit.stored, cached=True))

    running = ctx.flights.get(call.request_hash)
    if running is None:
        traj.delegated = True
        running = asyncio.create_task(_lead(ctx, call, traj, shared=True))
        ctx.flights[call.request_hash] = running
        running.add_done_callback(_forget_flight(ctx, call.request_hash))
        # The leader's own result carries its real cost; _lead has already finalised its run.
        return _outcome(traj.run_id, await asyncio.shield(running))

    await traj.event("single_flight_join", scope="process", request_hash=call.request_hash)
    return await _finish(traj, (await asyncio.shield(running)).free())


def _forget_flight(ctx: FarmContext, key: str) -> Callable[[asyncio.Task[Any]], None]:
    def done(task: asyncio.Task[Any]) -> None:
        if ctx.flights.get(key) is task:
            del ctx.flights[key]
        if not task.cancelled() and task.exception() is not None:
            log.error("router.execution_failed", request_hash=key, error=type(task.exception()).__name__)

    return done


async def _reject(traj: Trajectory, message: str) -> RouteOutcome:
    await traj.event("failure", error_kind=ErrorKind.BAD_REQUEST, error=message)
    return await _finish(traj, _settled_error(_error(ErrorKind.BAD_REQUEST, message)))


async def _record_cache_hit(
    traj: Trajectory,
    call: _Call,
    stored: flight.StoredResult,
    expires_at: datetime,
    original_run_id: UUID | None,
) -> None:
    await traj.event(
        "cache_hit",
        stored.connection_id,
        request_hash=call.request_hash,
        provider=stored.provider,
        expires_at=expires_at,
        original_run_id=original_run_id,
    )


async def _finish(traj: Trajectory, settled: _Settled) -> RouteOutcome:
    """Finalise the run of a caller and build its envelope."""
    await _finalise(traj, settled)
    return _outcome(traj.run_id, settled)


async def _finalise(traj: Trajectory, settled: _Settled) -> _Settled:
    await traj.finish(
        status=settled.status,
        cost_usd=settled.cost_usd,
        cached=settled.cached,
        connection_id=settled.connection_id,
        error_kind=None if settled.error is None else settled.error.kind,
        error=None if settled.error is None else settled.error.message,
    )
    return settled


async def _lead(ctx: FarmContext, call: _Call, traj: Trajectory, *, shared: bool) -> _Settled:
    """Execute the request as its leader and finalise the leader's run.

    ``shared`` = take part in cache / single-flight through the ``capability_requests`` row; ``False`` (pinned
    calls) executes without a row. Whatever happens, the run does not stay 'running' and a held row does not
    stay 'running' (its waiters would otherwise wait out the lease).
    """
    request_id = traj.run_id
    holds_row = False
    try:
        if shared:
            claimed = await flight.claim(
                ctx.pool,
                ctx.clock,
                request_hash=call.request_hash,
                capability=call.capability,
                params=call.params,
                run_id=traj.run_id,
                lease_s=ctx.deadline_s + LEASE_MARGIN_S,
                poll_s=ctx.poll_interval_s,
            )
            if isinstance(claimed, flight.Hit):
                await _record_cache_hit(traj, call, claimed.stored, claimed.expires_at, claimed.run_id)
                return await _finalise(traj, _settled_from_stored(claimed.stored, cached=True))
            if isinstance(claimed, flight.Joined):
                await traj.event(
                    "single_flight_join",
                    scope="cross_process",
                    request_hash=call.request_hash,
                    leader_run_id=claimed.run_id,
                )
                if claimed.stored is not None:
                    return await _finalise(traj, _settled_from_stored(claimed.stored, cached=False))
                error = (
                    RouteError.model_validate(claimed.error)
                    if claimed.error
                    else _error(INTERNAL, "the execution that was running elsewhere failed")
                )
                return await _finalise(traj, _settled_error(error))
            request_id = claimed.request_id
            holds_row = True
            await traj.attach_request(request_id)

        chain = _Chain(ctx, call, traj, request_id)
        limit = asyncio.timeout(ctx.deadline_s)
        try:
            async with limit:
                settled = await chain.run()
        except TimeoutError:
            if not limit.expired():
                raise
            settled = chain.settled_failure(
                _error(
                    ErrorKind.TIMEOUT,
                    f"the request did not finish within {ctx.deadline_s:g}s",
                    chain.attempts,
                )
            )
        if holds_row:
            await _publish(ctx, call, request_id, traj, settled)
        return await _finalise(traj, settled)
    except BaseException as exc:
        if holds_row:
            await asyncio.shield(
                flight.finish_failure(
                    ctx.pool,
                    request_id,
                    traj.run_id,
                    _error(INTERNAL, f"{type(exc).__name__}: {exc}").model_dump(mode="json"),
                    ctx.clock(),
                )
            )
        await _abort(traj, exc, owner=True)
        raise


async def _publish(
    ctx: FarmContext, call: _Call, request_id: UUID, traj: Trajectory, settled: _Settled
) -> None:
    """Make the leader's result visible to cache readers and to waiters in other processes."""
    now = ctx.clock()
    if settled.ok and settled.data is not None and settled.provider and settled.connection_id:
        # An inconclusive or empty answer (found = False) is not worth keeping: the next call asks again.
        ttl = 0 if settled.found is False else call.cap.cache_ttl_seconds
        await flight.finish_success(
            ctx.pool,
            request_id,
            traj.run_id,
            flight.StoredResult(
                data=settled.data,
                provider=settled.provider,
                connection_id=settled.connection_id,
                found=settled.found,
            ),
            now + timedelta(seconds=ttl),
        )
    elif settled.error is not None:
        await flight.finish_failure(
            ctx.pool, request_id, traj.run_id, settled.error.model_dump(mode="json"), now
        )
