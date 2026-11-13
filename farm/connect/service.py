"""LoginService: signs accounts in from the dashboard, one session per connection, in a staging home.

A dashboard command starts a session. A driver runs the provider's login in a *staging* copy of the account's
home and reports events (a link, a device code, "paste the code"), which the session row shows to the person.
Sealed input from the dashboard reaches the driver. On success the Farm checks who signed in (a second
sign-in as an account the pool already has is refused), asks the account for its models, runs a test job,
and only then does the staging home replace the real one. Every other outcome discards the staging home,
so a failed or abandoned (re)login never touches a working account. See ``docs/design/CONNECT.md`` §5.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from psycopg import errors
from psycopg.types.json import Jsonb

from farm.connect import sessions
from farm.connect.drivers.base import (
    Failure,
    Identity,
    LoginDriver,
    NeedsInput,
    NeedsVerification,
    Success,
    Url,
    UserCode,
)
from farm.connect.drivers.mcp_oauth import McpOAuthCallbacks, McpOAuthDriver
from farm.connect.homes import account_home, discard, promote, staging_home
from farm.connect.sealed import load_or_create_keys, open_sealed, publish_public_key
from farm.db.pool import Conn, DbPool, tenant_connection
from farm.executors.base import ConnectionView
from farm.executors.mcp.client import McpExecutor
from farm.mcp import store as mcp_store
from farm.mcp.expose import is_allowed
from farm.registry.models import McpProviderSpec
from farm.secrets import redact, register_secret
from farm.settings import data_dir
from farm.tenancy import use_tenant

log = structlog.get_logger(__name__)

METHODS = ("device_code", "paste_code", "loopback", "oauth_redirect", "api_key", "token")
EXCERPT_MAX = 500
API_KEY_CLIS = frozenset({"perplexity"})
"""AIs with no sign-in session: the account is an API key saved with ``save_secret``."""


class LoginError(Exception):
    """A request the service refuses; the message is shown to the person as it is."""


@dataclass(frozen=True)
class CheckResult:
    """What the test job reports for a freshly signed-in account (its run id is the evidence)."""

    ok: bool
    run_id: str | None = None
    detail: str | None = None


# (provider_id, connection_id, connection meta) -> the driver for that account's login.
type DriverFactory = Callable[[str, str, Mapping[str, Any]], LoginDriver]

# (connection_id, staging home) -> the result of a tiny job run with that login.
type TestJob = Callable[[str, Path], Awaitable[CheckResult]]


@dataclass
class _Run:
    session_id: UUID
    tenant_id: UUID
    connection_id: str
    provider_id: str
    method: str
    meta: Mapping[str, Any]
    actor: str
    driver: LoginDriver
    home: Path | None = None
    staging: Path | None = None
    task: asyncio.Task[None] | None = None
    cancel_reason: str | None = None
    finished: bool = False
    committing: bool = False


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _excerpt(text: str | None) -> str | None:
    if text is None:
        return None
    return redact(text)[:EXCERPT_MAX]


class LoginService:
    """Runs login sessions as asyncio tasks inside the Farm process (the command consumer's role)."""

    def __init__(
        self,
        pool: DbPool,
        *,
        driver_factory: DriverFactory,
        test_job: TestJob,
        keys_dir: Path | None = None,
        clock: Callable[[], datetime] = _utcnow,
        max_concurrent: int = 3,
        user_timeout_s: float = 600.0,
        verify_timeout_s: float = 120.0,
        sweep_interval_s: float = 15.0,
        verify_with_test: bool = False,
        mcp_executor: McpExecutor | None = None,
        mcp_callbacks: McpOAuthCallbacks | None = None,
    ) -> None:
        self._pool = pool
        self._driver_factory = driver_factory
        self._test_job = test_job
        self._keys_dir = keys_dir
        self._clock = clock
        self._slots = asyncio.Semaphore(max_concurrent)
        self._user_timeout_s = user_timeout_s
        self._verify_timeout_s = verify_timeout_s
        self._sweep_interval_s = sweep_interval_s
        # Inference can consume credits. Enrollment verifies identity, never silently sends a prompt.
        self._verify_with_test = verify_with_test
        self._mcp_executor = mcp_executor
        self._mcp_callbacks = mcp_callbacks or McpOAuthCallbacks()
        self._runs: dict[UUID, _Run] = {}
        self._keys: tuple[Any, dict[str, Any], bytes] | None = None
        self._sweeper: asyncio.Task[None] | None = None

    # --- lifecycle ----------------------------------------------------------------------------------

    async def start(self) -> None:
        """Load (or create) the sealing keys, publish the public key, end sessions a previous process left."""
        self._ensure_keys()
        await publish_public_key(self._pool, self._public_jwk())
        left = await sessions.fail_active_after_restart(self._pool)
        if left:
            log.info("connect.sessions_failed_after_restart", count=left)
        self._sweeper = asyncio.create_task(self._sweep(), name="connect-login-sweeper")

    async def stop(self) -> None:
        """Stop the sweeper and every running session (each ends ``failed`` / ``farm_restart``)."""
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None
        runs = list(self._runs.values())
        for run in runs:
            run.cancel_reason = run.cancel_reason or "farm_restart"
            if run.task is not None and not run.committing:
                run.task.cancel()
        await asyncio.gather(*(run.task for run in runs if run.task is not None), return_exceptions=True)

    # --- requests from commands ---------------------------------------------------------------------------

    async def start_login(
        self, tenant_id: UUID, connection_id: str, actor: str, method: str | None = None
    ) -> UUID:
        """Start signing ``connection_id`` in; returns the session id (the running one if there is one)."""
        if method is not None and method not in METHODS:
            raise LoginError(f"unknown sign-in method '{method}'")
        with use_tenant(tenant_id):
            existing = await sessions.active_for(self._pool, connection_id)
            if existing is not None:
                return UUID(str(existing["id"]))
            provider_id, meta = await self._connection(connection_id)
            mcp_driver = await self._mcp_driver(connection_id, provider_id, meta)
            if mcp_driver is None and os.environ.get("FARM_SHARED_INSTANCE") == "1":
                raise LoginError(
                    "Sign CLI accounts into a personal Farm. The shared gateway hosts shared tools."
                )
            if mcp_driver is not None and method not in (None, mcp_driver.method):
                raise LoginError("This MCP connection uses browser OAuth.")
            cli = str(meta.get("cli") or provider_id)
            if mcp_driver is None and cli in API_KEY_CLIS:
                raise LoginError(
                    f"{connection_id} is a {cli.title()} account: it has no sign-in. "
                    "Paste an API key for it (save_secret with this account) instead."
                )
            driver: LoginDriver | None
            failure: str | None = None
            try:
                driver = mcp_driver or self._driver_factory(provider_id, connection_id, meta)
                chosen = method or driver.method
            except NotImplementedError as exc:
                driver, chosen, failure = None, method or "loopback", str(exc) or "not available yet"
            try:
                session_id = await sessions.create(
                    self._pool,
                    connection_id=connection_id,
                    provider_id=provider_id,
                    method=chosen,
                    created_by=actor,
                )
            except errors.UniqueViolation:
                again = await sessions.active_for(self._pool, connection_id)
                if again is None:
                    raise
                return UUID(str(again["id"]))
            await self._audit_only(connection_id, actor, session_id, "queued")
            if driver is None:
                message = f"Signing in to {provider_id} from the dashboard is not available yet: {failure}"
                await self._finish_row(
                    session_id,
                    connection_id,
                    actor,
                    "failed",
                    error_kind="not_implemented",
                    error_excerpt=_excerpt(message),
                )
                return session_id
            run = _Run(
                session_id=session_id,
                tenant_id=tenant_id,
                connection_id=connection_id,
                provider_id=provider_id,
                method=chosen,
                meta=meta,
                actor=actor,
                driver=driver,
            )
            self._runs[session_id] = run
            run.task = asyncio.create_task(self._run(run), name=f"connect-login-{session_id}")
            return session_id

    async def submit_input(
        self, tenant_id: UUID, session_id: UUID, envelope: Mapping[str, Any], actor: str
    ) -> str:
        """Open the sealed text the person pasted and hand it to the waiting driver; returns the state."""
        run = self._runs.get(session_id)
        with use_tenant(tenant_id):
            row = await sessions.get(self._pool, session_id)
        if row is None or run is None or run.tenant_id != tenant_id:
            raise LoginError("This sign-in is not running any more. Start it again.")
        if row["state"] != "waiting_for_user" or not row["needs_input"]:
            raise LoginError("This sign-in is not waiting for a code right now.")
        aad = f"{tenant_id}|{session_id}|login_input"
        text = open_sealed(dict(envelope), private_key=self._private_key(), aad=aad)
        register_secret(text)
        try:
            await run.driver.submit(text)
        except ValueError:
            if isinstance(run.driver, McpOAuthDriver):
                raise LoginError("Paste the complete redirect URL from this sign-in attempt.") from None
            raise
        with use_tenant(tenant_id):
            await self._set(run, None, needs_input=False)
        return str(row["state"])

    async def cancel(self, tenant_id: UUID, session_id: UUID, actor: str) -> str:
        """Stop a session; the account stays exactly as it was."""
        run = self._runs.get(session_id)
        if run is not None and run.tenant_id == tenant_id and run.task is not None:
            if run.committing:
                await asyncio.shield(run.task)
                with use_tenant(tenant_id):
                    committed = await sessions.get(self._pool, session_id)
                return str(committed["state"]) if committed else "failed"
            run.cancel_reason = "cancelled"
            run.actor = actor
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
            if isinstance(run.driver, McpOAuthDriver) and not run.finished:
                with use_tenant(tenant_id):
                    await self._finish(run, "cancelled")
                self._runs.pop(run.session_id, None)
            return "cancelled"
        with use_tenant(tenant_id):
            row = await sessions.get(self._pool, session_id)
            if row is None:
                raise LoginError("There is no such sign-in.")
            if row["finished_at"] is None:
                await self._finish_row(session_id, str(row["connection_id"]), actor, "cancelled")
                return "cancelled"
            return str(row["state"])

    def running(self) -> list[UUID]:
        return list(self._runs)

    def open_input(self, envelope: Mapping[str, Any], aad: str) -> str:
        """Open a value the dashboard sealed for this Farm (``SealedError`` if sealed for another AAD)."""
        text = open_sealed(dict(envelope), private_key=self._private_key(), aad=aad)
        register_secret(text)
        return text

    def check_enrollment(self, provider: str, cli: str) -> None:
        """Reject unsupported setup before allocating an account or touching authentication."""
        if cli not in API_KEY_CLIS:
            driver = self._driver_factory(provider, "enrollment-preflight", {"cli": cli})
            preflight = getattr(driver, "preflight", None)
            if preflight is not None:
                try:
                    preflight()
                except FileNotFoundError as exc:
                    raise LoginError(str(exc)) from exc

    async def refresh_models(self, connection_id: str, retry_model: str | None = None) -> dict[str, Any]:
        """Read supported metadata only. Catalog membership never proves inference entitlement."""
        provider, meta = await self._connection(connection_id)
        if meta.get("cli") == "agy":
            from farm.executors.cli_agent.agy import AgyCliExecutor

            if os.name == "nt" and not meta.get("cli_prefix"):
                raise NotImplementedError("Antigravity model discovery requires an isolated runner profile.")
            models = await AgyCliExecutor().list_models(ConnectionView(
                id=connection_id, provider_id=provider, auth_ref=f"cli:{connection_id}", meta=dict(meta),
            )) or []
            source = "antigravity_cli_models"
        else:
            driver = self._driver_factory(provider, connection_id, meta)
            models = list(await driver.models(account_home(dict(meta), connection_id)))
            source = getattr(driver, "model_source", "provider_listing")
        observation = {
            "status": "observed" if models else "unknown",
            "models": models,
            "access": "unknown",
            "source": source,
            "observed_at": self._clock().isoformat() if models else None,
            "checked_at": self._clock().isoformat(),
        }
        historical = meta.get("models")
        historical = historical if isinstance(historical, list) else []
        async with tenant_connection(self._pool) as conn:
            await conn.execute(
                "update public.connections set meta=coalesce(meta,'{}') || %s where id=%s",
                (
                    Jsonb(
                        {
                            "model_discovery": observation,
                            "models": list(dict.fromkeys([*historical, *models])),
                        }
                    ),
                    connection_id,
                ),
            )
            if retry_model:
                await conn.execute(
                    "update public.connections set meta=jsonb_set(meta,'{rejected_models}',"
                    "coalesce(meta->'rejected_models','{}') - %s::text) where id=%s",
                    (retry_model.casefold(), connection_id),
                )
        return observation

    # --- one session --------------------------------------------------------------------------------------

    async def _run(self, run: _Run) -> None:
        with use_tenant(run.tenant_id):
            try:
                async with self._slots:
                    await self._drive(run)
            except asyncio.CancelledError:
                if run.cancel_reason == "cancelled":
                    await self._finish(run, "cancelled")
                else:
                    await self._finish(
                        run, "failed", error_kind="farm_restart", error_excerpt=sessions.RESTART_MESSAGE
                    )
                raise
            except LoginError as exc:
                await self._finish(run, "failed", error_kind="configuration_changed", error_excerpt=str(exc))
            except Exception as exc:
                log.warning("connect.login_crashed", session_id=str(run.session_id), error=type(exc).__name__)
                await self._finish(
                    run, "failed", error_kind="crash", error_excerpt=_excerpt(f"{type(exc).__name__}: {exc}")
                )
            finally:
                self._runs.pop(run.session_id, None)

    async def _drive(self, run: _Run) -> None:
        if isinstance(run.driver, McpOAuthDriver):
            run.staging = run.driver.staging_dir(run.tenant_id, run.session_id)
        else:
            run.home = account_home(dict(run.meta), run.connection_id)
            run.staging = staging_home(run.home, str(run.session_id))
        await self._set(run, "starting")
        hint = run.meta.get("expected_email")
        await run.driver.start(run.staging, hint if isinstance(hint, str) else None)
        deadline = time.monotonic() + self._user_timeout_s
        expires_at = self._clock() + timedelta(seconds=self._user_timeout_s)
        events: AsyncIterator[Any] = run.driver.events().__aiter__()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await self._expire(run)
                return
            try:
                event = await asyncio.wait_for(anext(events), timeout=remaining)
            except StopAsyncIteration:
                await self._finish(
                    run, "failed", error_kind="ended", error_excerpt="The sign-in ended without success."
                )
                return
            except TimeoutError:
                await self._expire(run)
                return
            if isinstance(event, Url):
                await self._set(run, "waiting_for_user", auth_url=event.url, expires_at=expires_at)
            elif isinstance(event, UserCode):
                await self._set(
                    run,
                    "waiting_for_user",
                    user_code=event.code,
                    verification_uri=event.verification_uri,
                    expires_at=expires_at,
                )
            elif isinstance(event, NeedsInput):
                await self._set(
                    run, "waiting_for_user", needs_input=True, input_hint=event.hint, expires_at=expires_at
                )
            elif isinstance(event, NeedsVerification):
                await self._finish(
                    run, "needs_verification", auth_url=event.url, error_excerpt=_excerpt(event.message)
                )
                return
            elif isinstance(event, Failure):
                await self._finish(
                    run, "failed", error_kind=event.kind, error_excerpt=_excerpt(event.excerpt)
                )
                return
            elif isinstance(event, Success):
                break
        if isinstance(run.driver, McpOAuthDriver):
            await self._commit_mcp(run, run.driver)
            return
        try:
            async with asyncio.timeout(self._verify_timeout_s):
                await self._verify(run)
        except TimeoutError:
            await self._finish(
                run,
                "failed",
                error_kind="verify_timeout",
                error_excerpt="Signed in, but checking the account took too long. Start again.",
            )

    async def _verify(self, run: _Run) -> None:
        assert run.staging is not None and run.home is not None
        await self._set(run, "verifying", needs_input=False)
        identity = await run.driver.identity(run.staging)
        if identity is None:
            await self._finish(
                run,
                "failed",
                error_kind="no_identity",
                error_excerpt="Signed in, but the Farm could not tell which account this is.",
            )
            return
        account_hash = self._account_hash(run.tenant_id, run.provider_id, identity.provider_account_id)
        shown = _identity_json(identity, account_hash)
        shown["verified_at"] = self._clock().isoformat()
        expected = str(run.meta.get("expected_email") or "").strip().casefold()
        if expected and identity.email.strip().casefold() != expected:
            await self._finish(
                run,
                "failed",
                identity=shown,
                error_kind="identity_mismatch",
                error_excerpt=(
                    "The verified sign-in does not match the expected email. Nothing was saved. "
                    "Copy the sign-in link into a private browser window and use the expected account."
                ),
            )
            return
        twin = await self._twin(run, account_hash)
        if twin is not None:
            await self._finish(
                run,
                "duplicate",
                identity=shown,
                error_kind="duplicate",
                error_excerpt=(
                    f"You signed in as {shown.get('email_masked') or 'an account'}, which is already {twin}. "
                    f"Nothing was saved and {twin} is untouched. Use a private window and sign in as the "
                    "right account."
                ),
            )
            return
        models = list(await run.driver.models(run.staging))
        # A driver that knows how to test its CLI does it itself (a one-shot prompt with the staging login).
        own_test = getattr(run.driver, "test_login", None)
        if not self._verify_with_test:
            check = CheckResult(ok=True)
        elif own_test is not None:
            check = await own_test(run.staging)
        else:
            check = await self._test_job(run.connection_id, run.staging)
        evidence: dict[str, Any] = {
            "test_run_id": check.run_id,
            "models": models,
            "test_ok": check.ok if self._verify_with_test else None,
            "test_status": "passed" if self._verify_with_test and check.ok else "not_run",
            "identity_verified": True,
        }
        quota_reader = getattr(run.driver, "quota", None)
        quota: dict[str, Any] = {"status": "unsupported", "error_code": "provider_quota_unsupported"}
        if quota_reader is not None:
            try:
                quota = await quota_reader(run.staging)
            except Exception:
                quota = {"status": "error", "error_code": "probe_failed"}
        evidence["quota_status"] = quota.get("status", "unknown")
        if not check.ok:
            await self._finish(
                run,
                "failed",
                identity=shown,
                evidence=evidence,
                error_kind="test_failed",
                error_excerpt=_excerpt(check.detail or "The test job with this login did not succeed."),
            )
            return
        # A driver may swap only its login files into an existing home (keeping settings and history); without
        # one the whole staging home replaces the account's home.
        own_promote = getattr(run.driver, "promote", None)
        if own_promote is not None:
            await asyncio.to_thread(own_promote, run.staging, run.home)
            await asyncio.to_thread(discard, run.staging)  # gone already after a whole-home swap
        else:
            await asyncio.to_thread(promote, run.staging, run.home)
        run.staging = None  # promoted: nothing left to discard
        await self._activate(run, shown, models, quota)
        await self._finish(run, "done", identity=shown, evidence=evidence)

    async def _expire(self, run: _Run) -> None:
        await self._finish(
            run,
            "expired",
            error_kind="expired",
            error_excerpt="Nobody finished this sign-in in time. Start it again.",
        )

    # --- database -----------------------------------------------------------------------------------------

    async def _mcp_driver(
        self, connection_id: str, provider_id: str, meta: Mapping[str, Any]
    ) -> McpOAuthDriver | None:
        if self._mcp_executor is None:
            return None
        async with tenant_connection(self._pool) as conn:
            cur = await conn.execute(
                "select c.auth_ref, p.executor from public.connections c "
                "join public.providers p on p.id = c.provider_id where c.id = %s",
                (connection_id,),
            )
            row = await cur.fetchone()
        if row is None or row[1] != "mcp":
            return None
        if meta.get("expected_email"):
            raise LoginError("This MCP server needs a provider identity check to verify the expected email.")
        providers = await mcp_store.list_providers(self._pool, provider_id=provider_id, enabled_only=False)
        if not providers:
            raise LoginError("This MCP provider has no browser OAuth configuration.")
        if not str(row[0]).startswith("token-store:"):
            raise LoginError("Browser OAuth needs an MCP token store reference.")
        try:
            return McpOAuthDriver(
                self._mcp_executor,
                ConnectionView(id=connection_id, provider_id=provider_id, auth_ref=row[0], meta=dict(meta)),
                providers[0].spec,
                self._mcp_callbacks,
            )
        except ValueError as exc:
            raise LoginError(str(exc)) from None

    async def _commit_mcp(self, run: _Run, driver: McpOAuthDriver) -> None:
        """Publish the validated generation and successful session in one transaction."""
        assert driver.new_ref is not None
        await self._set(run, "verifying", needs_input=False, auth_url=None)
        run.committing = True
        observed_at = self._clock()
        kept = [tool for tool in driver.tools if is_allowed(tool.name, driver.spec.tools)]
        evidence = {
            "tools_count": len(kept),
            "auth_verified": True,
            "identity_verified": False,
        }
        async with tenant_connection(self._pool) as conn, conn.transaction():
            cur = await conn.execute(
                "select config -> 'mcp' from public.providers where id = %s and executor = 'mcp' for update",
                (run.provider_id,),
            )
            provider = await cur.fetchone()
            if provider is None or McpProviderSpec.model_validate(provider[0]) != driver.spec:
                raise LoginError("The MCP configuration changed. Start sign-in again.")
            cur = await conn.execute(
                "update public.connections set auth_ref = %s, "
                "status = case when status = 'needs_login' then 'active' else status end, "
                "meta = coalesce(meta, '{}'::jsonb) || %s "
                "where id = %s and provider_id = %s and auth_ref = %s and coalesce(meta, '{}') = %s",
                (
                    driver.new_ref,
                    Jsonb({"login": {"method": run.method, "last_login_at": observed_at.isoformat()}}),
                    run.connection_id,
                    run.provider_id,
                    driver.connection.auth_ref,
                    Jsonb(dict(run.meta)),
                ),
            )
            if cur.rowcount != 1:
                raise LoginError("The MCP connection changed. Start sign-in again.")
            # The provider catalogue may already have been discovered under another explicit connection.
            cur = await conn.execute(
                "select 1 from public.mcp_tools where provider = %s limit 1", (run.provider_id,)
            )
            if await cur.fetchone() is None:
                await mcp_store.replace_tools_in(conn, run.provider_id, kept, observed_at)
            changed = await sessions.update_in(
                conn, run.session_id, state="done", needs_input=False, auth_url=None, evidence=evidence,
            )
            if not changed:
                raise LoginError("This MCP sign-in is no longer active.")
            await write_audit(
                conn, run.actor, "connection.login", run.connection_id, {"session_id": str(run.session_id)}
            )
        run.staging = None  # This generation is now the active token store.
        run.finished = True
        await driver.executor.discard(run.connection_id)

    async def _connection(self, connection_id: str) -> tuple[str, Mapping[str, Any]]:
        async with tenant_connection(self._pool) as conn:
            cur = await conn.execute(
                "select provider_id, meta from public.connections where id = %s", (connection_id,)
            )
            row = await cur.fetchone()
        if row is None:
            raise LoginError(f"There is no account '{connection_id}'.")
        meta = row[1] if isinstance(row[1], Mapping) else {}
        return str(row[0]), meta

    async def _twin(self, run: _Run, account_hash: str) -> str | None:
        """Another connection of the same provider in this tenant that is already this account."""
        async with tenant_connection(self._pool) as conn:
            cur = await conn.execute(
                "select id from public.connections where provider_id = %s and id <> %s "
                "and meta -> 'identity' ->> 'account_hash' = %s order by id limit 1",
                (run.provider_id, run.connection_id, account_hash),
            )
            row = await cur.fetchone()
        return None if row is None else str(row[0])

    async def _activate(
        self, run: _Run, shown: Mapping[str, Any], models: list[str], quota: dict[str, Any]
    ) -> None:
        patch: dict[str, Any] = {
            "identity": dict(shown),
            "login": {"method": run.method, "last_login_at": self._clock().isoformat()},
            "model_discovery": {
                "status": "observed" if models else "unknown",
                "source": getattr(run.driver, "model_source", "provider_listing"),
                "observed_at": self._clock().isoformat() if models else None,
                "checked_at": self._clock().isoformat(),
                "models": models,
            },
            "verified_models": models,
        }
        # Historical/operator configuration remains visible, but is not proof of current model access.
        if models:
            configured = run.meta.get("models")
            configured = configured if isinstance(configured, list) else []
            patch["models"] = list(dict.fromkeys([*configured, *models]))
        async with tenant_connection(self._pool) as conn, conn.transaction():
            await conn.execute(
                "update public.connections set "
                "status = case when status = 'needs_login' then 'active' else status end, "
                "meta = coalesce(meta, '{}'::jsonb) || %s where id = %s",
                (Jsonb(patch), run.connection_id),
            )
            if quota.get("status") == "ok":
                await conn.execute(
                    "insert into public.ai_usage_snapshots "
                    "(connection_id,provider_id,status,observed_at,checked_at,snapshot) "
                    "values(%s,%s,'ok',%s,%s,%s) on conflict(tenant_id,connection_id) do update set "
                    "status='ok',error_code=null,error_streak=0,observed_at=excluded.observed_at,"
                    "checked_at=excluded.checked_at,snapshot=excluded.snapshot",
                    (run.connection_id, run.provider_id, self._clock(), self._clock(), Jsonb(quota)),
                )
            await write_audit(
                conn, run.actor, "connection.login", run.connection_id, {"session_id": str(run.session_id)}
            )

    async def _set(self, run: _Run, state: str | None, **fields: Any) -> None:
        async with tenant_connection(self._pool) as conn, conn.transaction():
            changed = await sessions.update_in(conn, run.session_id, state=state, **fields)
            if changed and state is not None:
                await write_audit(
                    conn,
                    run.actor,
                    f"login_session.{state}",
                    run.connection_id,
                    {"session_id": str(run.session_id), "state": state},
                )

    async def _finish(self, run: _Run, state: str, **fields: Any) -> None:
        """End the session; anything but ``done`` stops the driver and discards the staging home."""
        if run.finished:
            return
        run.finished = True
        with contextlib.suppress(Exception):
            await run.driver.cancel()  # drivers make cancel idempotent; after success it just reaps the CLI
        if state != "done":
            if run.staging is not None:
                await asyncio.to_thread(discard, run.staging)
                run.staging = None
        if isinstance(run.driver, McpOAuthDriver):
            fields.update(auth_url=None, needs_input=False)
        await self._set(run, state, **fields)

    async def _finish_row(
        self, session_id: UUID, connection_id: str, actor: str, state: str, **fields: Any
    ) -> None:
        async with tenant_connection(self._pool) as conn, conn.transaction():
            if await sessions.update_in(conn, session_id, state=state, **fields):
                await write_audit(
                    conn,
                    actor,
                    f"login_session.{state}",
                    connection_id,
                    {"session_id": str(session_id), "state": state},
                )

    async def _audit_only(self, connection_id: str, actor: str, session_id: UUID, state: str) -> None:
        async with tenant_connection(self._pool) as conn:
            await write_audit(
                conn,
                actor,
                f"login_session.{state}",
                connection_id,
                {"session_id": str(session_id), "state": state},
            )

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(self._sweep_interval_s)
            try:
                expired = await sessions.expire_overdue(self._pool)
                if expired:
                    log.info("connect.sessions_expired", count=expired)
            except Exception as exc:
                log.warning("connect.sweep_failed", error=type(exc).__name__)

    # --- keys ---------------------------------------------------------------------------------------------

    def _ensure_keys(self) -> tuple[Any, dict[str, Any], bytes]:
        if self._keys is None:
            keys_dir = self._keys_dir or (data_dir() / "keys")
            private_key, public_jwk, salt = load_or_create_keys(keys_dir)
            self._keys = (private_key, dict(public_jwk), bytes(salt))
        return self._keys

    def _private_key(self) -> Any:
        return self._ensure_keys()[0]

    def _public_jwk(self) -> dict[str, Any]:
        return self._ensure_keys()[1]

    def _account_hash(self, tenant_id: UUID, provider_id: str, provider_account_id: str) -> str:
        salt = self._ensure_keys()[2]
        message = f"{tenant_id}|{provider_id}|{provider_account_id}".encode()
        return hmac.new(salt, message, hashlib.sha256).hexdigest()


def _identity_json(identity: Identity, account_hash: str) -> dict[str, Any]:
    return {
        "account_hash": account_hash,
        "email": identity.email or None,
        "display_name": identity.display_name,
        "source": "provider_login",
        "email_masked": identity.masked_email() if identity.email else None,
        "plan": identity.plan,
        "org": identity.org,
    }


async def write_audit(conn: Conn, actor: str, action: str, target: str, after: Mapping[str, Any]) -> None:
    # The same row shape as farm.control.commands._emit_audit; written here so this module never imports the
    # command module (which imports this one).
    await conn.execute(
        "insert into public.audit_events (actor, action, target, before, after) values (%s, %s, %s, %s, %s)",
        (actor, action, target, None, Jsonb(dict(after))),
    )


_service: LoginService | None = None


def set_login_service(service: LoginService | None) -> None:
    """Register the Farm process's service (``farm run``), or clear it on shutdown."""
    global _service
    _service = service


def get_login_service() -> LoginService:
    if _service is None:
        raise LoginError("The sign-in service is not running in this Farm process.")
    return _service
