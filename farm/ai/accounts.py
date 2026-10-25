"""AI accounts as the job layer sees them: which exist, which can take work now, how busy, which to pick.

``load_accounts`` reads the connections of every ``kind = 'ai'`` provider together with their health, their
reset time and the jobs queued or running on them. An account's *usability* is the router's own rule
(``farm.resources.health.block_reason``: paused / disabled / needs_login / exhausted until the reset / cooling
down / circuit open) plus the scope and the model it must offer, so a job is never queued on an account the
router would refuse; the router stays the final judge when the job actually runs.

Parallelism per account is ``min(meta.max_parallel, connections.concurrency)``, and
``connections.concurrency``
when ``meta.max_parallel`` is absent (both default to 1): the router's per-account gate is
``connections.concurrency``, so asking the job layer for more than the gate lets through would only make jobs
wait inside the router. A whole pool can be capped with ``providers.config.max_parallel``.

Picking (``rank``): AIs in the route order of ``ask_ai`` (``ai = "any"`` therefore prefers the first AI of the
route), inside one AI the accounts that can start the job right away first, then by priority (lower first).

``list_ai_accounts`` (``list_ais``, ``farm ai list``) reports each account once, with the verdict the picking
uses (``usable``, else ``unusable`` and ``back_at``) and the last error as kind + time + one short line, so an
old error is never read as the account's state now.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, NamedTuple
from uuid import uuid4

from farm.ai.failures import AiRequestError
from farm.db.pool import DbPool, tenant_connection
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult, Executor
from farm.resources import health

ASK_AI = "ask_ai"
UNROUTED_RANK = 1000
PING: Final[Mapping[str, str]] = {"task": "Respond with only the word PONG", "mode": "answer"}
"""The tiny call of ``farm ai test`` and of a login re-check."""
DEFAULT_EFFORTS: Final[Mapping[str, tuple[str, ...]]] = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("low", "medium", "high"),
}
"""Efforts each CLI accepts when an account's ``meta.efforts`` does not say."""


@dataclass(frozen=True)
class AccountInfo:
    id: str
    ai: str
    label: str
    status: str
    priority: int
    concurrency: int
    scope: tuple[str, ...]
    meta: Mapping[str, Any]
    circuit: str
    cooldown_until: datetime | None
    next_reset_at: datetime | None
    last_error: str | None
    success_count: int
    provider_enabled: bool
    pool_max_parallel: int | None
    route_rank: int
    active: int = 0
    queued: int = 0
    last_error_kind: str | None = None
    last_error_at: datetime | None = None

    @property
    def efforts(self) -> tuple[str, ...]:
        raw = self.meta.get("efforts")
        if isinstance(raw, list):
            return tuple(str(e) for e in raw)
        if isinstance(raw, str):
            return (raw,)
        return DEFAULT_EFFORTS.get(self.ai, ())

    @property
    def models(self) -> tuple[str, ...]:
        raw = self.meta.get("models")
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, list):
            return tuple(str(m) for m in raw)
        single = self.meta.get("model")
        return (str(single),) if single else ()

    @property
    def configured_parallel(self) -> int | None:
        """``meta.max_parallel`` when it is a positive integer."""
        raw = self.meta.get("max_parallel")
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1 else None

    @property
    def max_parallel(self) -> int:
        configured = self.configured_parallel
        return max(1, min(self.concurrency if configured is None else configured, self.concurrency))

    @property
    def load(self) -> int:
        return self.active + self.queued

    @property
    def login_state(self) -> str:
        if self.status == "needs_login":
            return "needs_login"
        return "ok" if self.success_count > 0 else "unknown"

    def offers(self, model: str | None) -> bool:
        return model is None or model.lower() in {m.lower() for m in self.models}

    def models_check(self, accepted: Sequence[str] | None) -> ModelCheck:
        """This account's models against ``accepted``, the ones its CLI lists (``None``: cannot list)."""
        return ModelCheck(self.id, self.models, None if accepted is None else tuple(accepted))

    def unusable_reason(self, now: datetime, *, model: str | None = None) -> str | None:
        """Why a job cannot be started on this account now (the router's skip reasons), or ``None``."""
        if not self.provider_enabled:
            return "provider_disabled"
        blocked = health.block_reason(self.status, self.circuit, self.cooldown_until, now)
        if blocked is not None:
            return blocked
        if "internal" not in self.scope:
            return "scope"
        if not self.offers(model):
            return "model_not_offered"
        return None

    def retry_at(self, now: datetime) -> datetime | None:
        """When the account can take work again, if the Farm knows (a login or a person must fix the rest)."""
        if self.status in ("needs_login", "paused", "disabled"):
            return None
        if self.cooldown_until is not None and self.cooldown_until > now:
            return self.cooldown_until
        if self.status == "exhausted" and self.next_reset_at is not None and self.next_reset_at > now:
            return self.next_reset_at
        return None


@dataclass(frozen=True)
class ModelCheck:
    """The models an account offers (registry ``meta.models``) against the ones its CLI accepts.

    A CLI update can drop a model the registry still lists; jobs asking for it then fail. ``farm ai test``
    runs this check wherever the CLI can list its models (agy).
    """

    account: str
    configured: tuple[str, ...]
    accepted: tuple[str, ...] | None

    @property
    def rejected(self) -> list[str]:
        """Configured models the CLI does not accept (compared case-insensitively, as ``offers`` does)."""
        if self.accepted is None:
            return []
        known = {m.lower() for m in self.accepted}
        return [m for m in self.configured if m.lower() not in known]

    def describe(self) -> str:
        if self.accepted is None:
            return f"models: {self.account}'s CLI cannot list the models it accepts; not checked"
        accepted = ", ".join(self.accepted)
        if self.rejected:
            return (
                f"models: {self.account} lists {', '.join(self.rejected)}, which its CLI does not accept "
                f"(it accepts: {accepted}); remove them from the registry and run `farm registry sync`"
            )
        if not self.configured:
            return f"models: none configured for {self.account}; its CLI accepts: {accepted}"
        return f"models: its CLI accepts every model {self.account} lists ({', '.join(self.configured)})"


_ACCOUNTS_SQL = """
select c.id, c.provider_id, c.label, c.status, c.priority, c.concurrency, c.scope, c.meta,
       coalesce(h.circuit, 'closed'), h.cooldown_until, h.last_error, coalesce(h.success_count, 0),
       u.next_reset_at, p.enabled, p.config,
       coalesce((select min(r.position) from public.capability_routes r
                 where r.capability = %(cap)s and r.provider_id = p.id and r.enabled), %(unrouted)s),
       h.last_error_kind, h.last_error_at
from public.connections c
join public.providers p on p.id = c.provider_id and p.kind = 'ai'
left join public.connection_health h on h.connection_id = c.id
left join (select connection_id, max(next_reset_at) as next_reset_at
           from public.consumption_units group by connection_id) u on u.connection_id = c.id
where (%(ids)s::text[] is null or c.id = any(%(ids)s::text[]))
"""


def _pool_cap(config: Mapping[str, Any] | None) -> int | None:
    raw = (config or {}).get("max_parallel")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1 else None


async def load_accounts(pool: DbPool, *, ids: Sequence[str] | None = None) -> list[AccountInfo]:
    """AI accounts with their live state and job load, in pick order (route position, provider, priority)."""
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            _ACCOUNTS_SQL,
            {"cap": ASK_AI, "unrouted": UNROUTED_RANK, "ids": None if ids is None else list(ids)},
        )
        rows = await cur.fetchall()
        cur = await conn.execute(
            "select account, state, count(*) from public.ai_jobs where state in ('queued', 'running') "
            "group by account, state"
        )
        loads = await cur.fetchall()
    active = {str(a): int(n) for a, s, n in loads if s == "running"}
    queued = {str(a): int(n) for a, s, n in loads if s == "queued"}
    accounts = [
        AccountInfo(
            id=r[0],
            ai=r[1],
            label=r[2] or r[0],
            status=r[3],
            priority=r[4],
            concurrency=r[5],
            scope=tuple(r[6] or ()),
            meta=r[7] or {},
            circuit=r[8],
            cooldown_until=r[9],
            next_reset_at=r[12],
            last_error=r[10],
            success_count=int(r[11]),
            provider_enabled=bool(r[13]),
            pool_max_parallel=_pool_cap(r[14]),
            route_rank=int(r[15]),
            active=active.get(r[0], 0),
            queued=queued.get(r[0], 0),
            last_error_kind=r[16],
            last_error_at=r[17],
        )
        for r in rows
    ]
    return sorted(accounts, key=lambda a: (a.route_rank, a.ai, a.priority, a.id))


# --- picking ------------------------------------------------------------------------------------------


class Wish(NamedTuple):
    """What one job asks of the account choice: an AI (or ``any``), a model, perhaps one named account."""

    ai: str
    model: str | None
    account: str | None


def _when(at: datetime) -> str:
    return at.strftime("%Y-%m-%d %H:%MZ")


def describe_unusable(accounts: Sequence[AccountInfo], now: datetime, *, model: str | None) -> list[str]:
    """``claude-03 (exhausted, back 2026-10-05 14:30Z)``: one entry per account that cannot take a job now."""
    notes: list[str] = []
    for account in accounts:
        reason = account.unusable_reason(now, model=model)
        if reason is None:
            continue
        back = account.retry_at(now)
        notes.append(f"{account.id} ({reason}{'' if back is None else f', back {_when(back)}'})")
    return notes


def usable(
    accounts: Sequence[AccountInfo],
    *,
    ai: str,
    model: str | None,
    now: datetime,
    exclude: frozenset[str] = frozenset(),
) -> list[AccountInfo]:
    """Accounts of ``ai`` (``any`` = every AI) that can take a job with ``model`` now, unranked."""
    return [
        a
        for a in accounts
        if (ai == "any" or a.ai == ai) and a.id not in exclude and a.unusable_reason(now, model=model) is None
    ]


def rank(accounts: Sequence[AccountInfo], extra_load: Mapping[str, int] | None = None) -> list[AccountInfo]:
    """Best first: route order of the AIs, then the shortest wait for a new job, then priority.

    The wait is how many jobs must finish on the account first (0 = idle, or a free slot): idle accounts
    come before busy ones and a queue is built on the account with the fewest jobs ahead.
    """
    extra = extra_load or {}

    def key(a: AccountInfo) -> tuple[int, str, int, int, str]:
        ahead = max(0, a.load + extra.get(a.id, 0) + 1 - a.max_parallel)
        return (a.route_rank, a.ai, ahead, a.priority, a.id)

    return sorted(accounts, key=key)


def no_usable_account(
    accounts: Sequence[AccountInfo], *, ai: str, model: str | None, now: datetime
) -> AiRequestError:
    """The refusal for a job with no account to run on: which accounts exist and why none is usable."""
    wanted = [a for a in accounts if ai == "any" or a.ai == ai]
    label = "AI" if ai == "any" else ai
    only_ai = None if ai == "any" else ai
    if not wanted:
        return AiRequestError("bad_request", f"no {label} accounts are configured", ai=only_ai)
    retries = [at for a in wanted if (at := a.retry_at(now)) is not None]
    for_model = f" offering model '{model}'" if model else ""
    return AiRequestError(
        "account_unavailable",
        f"no usable {label} account{for_model}: " + "; ".join(describe_unusable(wanted, now, model=model)),
        ai=only_ai,
        retry_at=min(retries) if retries else None,
    )


def refuse_single(accounts: Sequence[AccountInfo], wish: Wish, now: datetime) -> AiRequestError:
    """Why one job (or reply) has no account: a named account that is missing, of another AI or unusable now,
    or no usable account at all. The account of a conversation is a named account."""
    if wish.account is None:
        return no_usable_account(accounts, ai=wish.ai, model=wish.model, now=now)
    account = next((a for a in accounts if a.id == wish.account), None)
    if account is None:
        return AiRequestError("bad_request", f"account '{wish.account}' does not exist", account=wish.account)
    if wish.ai not in ("any", account.ai):
        return AiRequestError(
            "bad_request",
            f"account '{account.id}' is a {account.ai} account, not {wish.ai}",
            ai=account.ai,
            account=account.id,
        )
    reason = account.unusable_reason(now, model=wish.model)
    back = account.retry_at(now)
    when = "" if back is None else f", back {_when(back)}"
    return AiRequestError(
        "account_unavailable",
        f"account '{account.id}' cannot take work now ({reason or 'busy'}{when})",
        ai=account.ai,
        account=account.id,
        retry_at=back,
    )


def plan_accounts(
    accounts: Sequence[AccountInfo], wishes: Sequence[Wish], *, distinct: bool, now: datetime
) -> list[AccountInfo]:
    """One account per wish, in wish order. Raises ``AiRequestError`` (nothing is started) when it cannot.

    ``distinct``: no two wishes get the same account. Named accounts are placed first; the others take the
    best usable account that is still free: idle accounts first, then queueing behind busy ones.
    """
    by_id = {a.id: a for a in accounts}
    plan: list[AccountInfo | None] = [None] * len(wishes)
    taken: set[str] = set()
    load: dict[str, int] = {}
    problems: list[str] = []

    def place(index: int, account: AccountInfo) -> None:
        plan[index] = account
        taken.add(account.id)
        load[account.id] = load.get(account.id, 0) + 1

    for index, wish in enumerate(wishes):
        if wish.account is None:
            continue
        account = by_id.get(wish.account)
        if account is None:
            problems.append(f"job {index}: account '{wish.account}' does not exist")
        elif wish.ai not in ("any", account.ai):
            problems.append(f"job {index}: account '{account.id}' is a {account.ai} account, not {wish.ai}")
        elif (reason := account.unusable_reason(now, model=wish.model)) is not None:
            problems.append(f"job {index}: account '{account.id}' is unavailable ({reason})")
        elif distinct and account.id in taken:
            problems.append(
                f"job {index}: account '{account.id}' is already used by another job (distinct_accounts)"
            )
        else:
            place(index, account)

    shortfalls: dict[tuple[str, str | None], int] = {}
    for index, wish in enumerate(wishes):
        if wish.account is not None:
            continue
        free = [
            a
            for a in usable(accounts, ai=wish.ai, model=wish.model, now=now)
            if not (distinct and a.id in taken)
        ]
        if not free:
            key = (wish.ai, wish.model)
            shortfalls[key] = shortfalls.get(key, 0) + 1
            continue
        place(index, rank(free, load)[0])

    for (ai, model), missing in shortfalls.items():
        label = "AI" if ai == "any" else ai
        of_ai = [a for a in accounts if ai == "any" or a.ai == ai]
        good = [a.id for a in usable(accounts, ai=ai, model=model, now=now)]
        wanted = sum(1 for w in wishes if (w.ai, w.model) == (ai, model) and w.account is None)
        offering = f" offering model '{model}'" if model else ""
        unavailable = describe_unusable(of_ai, now, model=model)
        problems.append(
            f"need {wanted} distinct {label} account(s){offering}, only {len(good)} usable "
            f"({', '.join(good) or 'none'}; {missing} job(s) would have no account)"
            + (f"; unavailable: {'; '.join(unavailable)}" if unavailable else "")
        )
    if problems:
        raise AiRequestError("not_enough_accounts" if shortfalls else "bad_request", "; ".join(problems))
    return [account for account in plan if account is not None]


# --- the report behind list_ais / farm ai list -----------------------------------------------------------


ERROR_CHARS: Final = 160
_LINK = re.compile(r"https?://\S+")


def _utc(at: datetime) -> str:
    """``2026-10-24T06:16:03Z``: UTC to the second (the database answers in its session time zone)."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_error(text: str, limit: int = ERROR_CHARS) -> str:
    """One line of at most ``limit`` characters; links (long, one-time sign-in URLs) become ``<link>``."""
    line = " ".join(_LINK.sub("<link>", text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def account_report(account: AccountInfo, now: datetime, calls_today: int) -> dict[str, Any]:
    """One account of ``list_ais``. Always: who it is, the picking rule's verdict (``usable``) and its load.
    Only when they apply: why it is unusable and until when (``unusable``, ``back_at``), a limit reset still
    ahead (``reset_at``), a circuit that is not closed, and the last error (kind, time, one short line)."""
    reason = account.unusable_reason(now)
    entry: dict[str, Any] = {
        "id": account.id,
        "ai": account.ai,
        "label": account.label,
        "status": account.status,
        "usable": reason is None,
    }
    if reason is not None:
        entry["unusable"] = reason
        back = account.retry_at(now)
        if back is not None:
            entry["back_at"] = _utc(back)
    entry.update(
        login_state=account.login_state,
        models=list(account.models),
        efforts=list(account.efforts),
        max_parallel=account.max_parallel,
        active_jobs=account.active,
        queued_jobs=account.queued,
        todays_calls=calls_today,
    )
    reset = account.next_reset_at or account.cooldown_until
    if reset is not None and reset > now:
        entry["reset_at"] = _utc(reset)
    if account.circuit != "closed":
        entry["circuit"] = account.circuit
    if account.last_error:
        entry["last_error"] = {
            "kind": account.last_error_kind,
            "at": _utc(account.last_error_at) if account.last_error_at else None,
            "message": short_error(account.last_error),
        }
    return entry


async def list_ai_accounts(pool: DbPool, now: datetime) -> dict[str, Any]:
    """Every AI pool (``ais``: usable / total accounts and job load) and every account once (``accounts``)."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    accounts = await load_accounts(pool)
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select id, name, enabled, config from public.providers where kind = 'ai' order by id"
        )
        providers = await cur.fetchall()
        cur = await conn.execute(
            "select connection_id, count(*) from public.runs "
            "where started_at >= %s and connection_id is not null group by connection_id",
            (today_start,),
        )
        call_counts: dict[str, int] = {str(c): int(n) for c, n in await cur.fetchall()}

    entries = [
        account_report(a, now, call_counts.get(a.id, 0))
        for a in sorted(accounts, key=lambda a: (a.ai, a.priority, a.id))
    ]
    ais: list[dict[str, Any]] = []
    for provider_id, name, enabled, config in providers:
        own = [e for e in entries if e["ai"] == provider_id]
        ais.append(
            {
                "id": provider_id,
                "name": name,
                "enabled": enabled,
                "usable": sum(1 for e in own if e["usable"]),
                "total": len(own),
                "max_parallel": _pool_cap(config),
                "active_jobs": sum(int(e["active_jobs"]) for e in own),
                "queued_jobs": sum(int(e["queued_jobs"]) for e in own),
            }
        )
    return {"ais": ais, "accounts": entries}


# --- re-checking an account marked needs_login ------------------------------------------------------------


async def recheck_login(
    pool: DbPool, executor: Executor, account_id: str, *, now: datetime, timeout_s: float = 180.0
) -> ExecResult:
    """One tiny call on an account marked ``needs_login``, straight through its executor.

    The router never routes to such an account, so before this only a registry sync brought it back: not
    ``farm ai login``, and not a state an old classifier set wrongly (codex-01 kept the ``needs_login`` that a
    misread Codex session error gave it, D4). An answer proves the login: the success is recorded and the
    account is ``active`` again. A failure is recorded like any other (a login failure keeps ``needs_login``).
    """
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select provider_id, auth_ref, meta, concurrency from public.connections where id = %s",
            (account_id,),
        )
        row = await cur.fetchone()
    if row is None:
        raise AiRequestError("bad_request", f"account '{account_id}' does not exist", account=account_id)
    view = ConnectionView(
        id=account_id, provider_id=row[0], auth_ref=row[1], meta=row[2] or {}, concurrency=row[3] or 1
    )
    request = ExecRequest(
        request_id=uuid4(), capability=ASK_AI, params=dict(PING), connection=view, timeout_s=timeout_s
    )
    result = await executor.execute(request)
    if result.ok and not str((result.data or {}).get("text", "")).strip():
        # A CLI that exits cleanly but says nothing is not a working account (agy did this on 2026-10-25).
        result = result.model_copy(
            update={"ok": False, "error_kind": ErrorKind.UNKNOWN, "error": "empty answer to the test prompt"}
        )
    if result.ok:
        await health.record_success(pool, account_id, now=now)
        async with tenant_connection(pool) as conn:
            await conn.execute(
                "update public.connections set status = 'active' where id = %s and status = 'needs_login'",
                (account_id,),
            )
    else:
        await health.record_failure(
            pool,
            account_id,
            result.error_kind or ErrorKind.UNKNOWN,
            result.error,
            now=now,
            retry_after_s=result.retry_after_s,
            reset_at=result.reset_at,
        )
    return result
