"""Dashboard commands for signing accounts in, handled by the LoginService (``farm_commands`` kinds).

``start_login``, ``submit_login_input``, ``cancel_login``, ``add_accounts`` and ``save_secret``. Pasted
codes and keys arrive sealed for this Farm (``farm.connect.sealed``); once such a command has run, the
command consumer stores ``"consumed"`` in place of the ciphertext (``consumed_payload``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from farm.connect.sealed import SealedEnvelope, SealedError
from farm.connect.service import API_KEY_CLIS, LoginError, get_login_service, write_audit
from farm.db.pool import DbPool, tenant_connection
from farm.secrets import set_secret
from farm.tenancy import require_tenant

_ID = r"^[a-z0-9][a-z0-9_-]{0,62}$"
_ENV_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"
_CLIS = ("claude", "codex", "gemini", "agy", "hermes", "perplexity")
_NAMES = {
    "claude": "Claude",
    "codex": "Codex",
    "gemini": "Gemini",
    "hermes": "Hermes",
    "perplexity": "Perplexity",
}
_TEMPLATE_KEYS = ("allow_edit", "edit_roots", "timeout_s")

Method = Literal["device_code", "paste_code", "loopback", "oauth_redirect", "api_key", "token"]


class StartLoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(pattern=_ID)
    method: Method | None = None


class SubmitLoginInputPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    sealed: SealedEnvelope


class CancelLoginPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: UUID


class AddAccountsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(pattern=_ID)
    count: int = Field(ge=1, le=10)
    cli: Literal["claude", "codex", "gemini", "agy", "hermes", "perplexity"] | None = None
    plan: str | None = Field(default=None, max_length=40)
    emails: list[str] = Field(default_factory=list, max_length=10)
    names: list[str] = Field(default_factory=list, max_length=10)
    request_id: UUID | None = None
    max_parallel: int = Field(default=1, ge=1, le=4)

    @model_validator(mode="after")
    def _emails_fit(self) -> AddAccountsPayload:
        if len(self.emails) != self.count:
            raise ValueError("supply one expected email per account")
        if len(self.names) > self.count or any(len(name) > 100 for name in self.names):
            raise ValueError("supply at most one name per account (100 characters maximum)")
        for email in self.emails:
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()):
                raise ValueError(f"'{email}' is not an email address")
        self.emails = [email.strip().lower() for email in self.emails]
        if len(set(self.emails)) != self.count:
            raise ValueError("expected emails must be distinct within a batch")
        return self


class SaveSecretPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=_ENV_NAME, max_length=100)
    sealed: SealedEnvelope
    connection_id: str | None = Field(default=None, pattern=_ID)


CONNECT_PAYLOADS: dict[str, type[BaseModel]] = {
    "start_login": StartLoginPayload,
    "submit_login_input": SubmitLoginInputPayload,
    "cancel_login": CancelLoginPayload,
    "add_accounts": AddAccountsPayload,
    "save_secret": SaveSecretPayload,
}
SEALED_KINDS = frozenset({"submit_login_input", "save_secret"})


def consumed_payload(kind: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """The payload kept once the command ran: the ciphertext becomes ``"consumed"`` (None: unchanged)."""
    if kind not in SEALED_KINDS or "sealed" not in payload:
        return None
    return {**payload, "sealed": "consumed"}


async def execute_connect_command(
    pool: DbPool, kind: str, payload: BaseModel, *, actor: str, command_id: UUID | None = None
) -> tuple[str, dict[str, Any]]:
    """Run one sign-in command for the current tenant; refusals come back as ``rejected`` with the reason."""
    tenant_id = require_tenant()
    try:
        if isinstance(payload, StartLoginPayload):
            session_id = await get_login_service().start_login(
                tenant_id, payload.connection_id, actor, payload.method
            )
            return "done", {"session_id": str(session_id), "connection_id": payload.connection_id}
        if isinstance(payload, SubmitLoginInputPayload):
            state = await get_login_service().submit_input(
                tenant_id, payload.session_id, payload.sealed.model_dump(), actor
            )
            return "done", {"session_id": str(payload.session_id), "state": state}
        if isinstance(payload, CancelLoginPayload):
            state = await get_login_service().cancel(tenant_id, payload.session_id, actor)
            return "done", {"session_id": str(payload.session_id), "state": state}
        if isinstance(payload, AddAccountsPayload):
            payload.request_id = payload.request_id or command_id
            return await _add_accounts(pool, tenant_id, payload, actor)
        if isinstance(payload, SaveSecretPayload):
            return await _save_secret(pool, tenant_id, payload, actor)
    except (LoginError, SealedError) as exc:
        return "rejected", {"error": str(exc)}
    return "rejected", {"error": f"Unknown sign-in command '{kind}'"}


async def _add_accounts(
    pool: DbPool, tenant_id: UUID, cmd: AddAccountsPayload, actor: str
) -> tuple[str, dict[str, Any]]:
    # Serialize allocation across consumers; the rows retain the request id across command redelivery.
    while True:
        async with tenant_connection(pool) as lock_conn, lock_conn.transaction():
            cur = await lock_conn.execute(
                "select pg_try_advisory_xact_lock(hashtextextended(current_setting('app.tenant_id'), 194733))"
            )
            row = await cur.fetchone()
            if row and row[0]:
                return await _add_accounts_locked(pool, tenant_id, cmd, actor)
        # Do not occupy every pool checkout waiting for a lock whose holder needs a checkout to create rows.
        await asyncio.sleep(0.05)


async def _add_accounts_locked(
    pool: DbPool, tenant_id: UUID, cmd: AddAccountsPayload, actor: str
) -> tuple[str, dict[str, Any]]:
    # Imported here: the command module imports this one.
    from farm.control.commands import execute_provider_command

    service = get_login_service()  # refuse before creating accounts nobody could sign in
    fingerprint = hashlib.sha256(
        json.dumps(cmd.model_dump(mode="json", exclude={"request_id", "plan"}), sort_keys=True).encode()
    ).hexdigest()
    async with tenant_connection(pool) as conn:
        cur = await conn.execute("select id, meta, provider_id from public.connections order by id")
        all_rows = await cur.fetchall()
        rows = [(str(r[0]), r[1] if isinstance(r[1], dict) else {}) for r in all_rows]
        provider_rows = [
            (str(r[0]), r[1] if isinstance(r[1], dict) else {}) for r in all_rows if r[2] == cmd.provider_id
        ]
    if cmd.request_id is not None and any(
        meta.get("enrollment_request_id") == str(cmd.request_id)
        and meta.get("enrollment_fingerprint") != fingerprint
        for _, meta in rows
    ):
        raise LoginError("This enrollment request id was already used for a different enrollment")
    cli = cmd.cli or _usual_cli(cmd.provider_id, provider_rows)
    try:
        service.check_enrollment(cmd.provider_id, cli)
    except NotImplementedError as exc:
        raise LoginError(str(exc)) from exc
    prefix = "agy-" if cli == "agy" else "gemini-cli-" if cli == "gemini" else f"{cmd.provider_id}-"
    template = next((meta for _, meta in provider_rows if meta.get("cli") == cli), {})
    taken = {conn_id for conn_id, _ in rows}
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    number = max((int(m.group(1)) for c in taken if (m := pattern.fullmatch(c))), default=0) + 1

    created: list[str] = []
    session_ids: list[str] = []
    accounts: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for index in range(cmd.count):
        previous = next(
            (
                c
                for c, meta in rows
                if cmd.request_id is not None
                and meta.get("enrollment_request_id") == str(cmd.request_id)
                and meta.get("enrollment_index") == index
            ),
            None,
        )
        if previous and next(m for c, m in rows if c == previous).get("expected_email") != cmd.emails[index]:
            raise LoginError("This enrollment request id was already used for a different expected account")
        while f"{prefix}{number:02d}" in taken:
            number += 1
        account_id = previous or f"{prefix}{number:02d}"
        number += 1
        email = cmd.emails[index] if index < len(cmd.emails) and cmd.emails[index] else None
        meta: dict[str, Any] = {key: template[key] for key in _TEMPLATE_KEYS if key in template}
        if email:
            meta["expected_email"] = email
        meta.update(enrollment_request_id=str(cmd.request_id), enrollment_index=index)
        meta["enrollment_fingerprint"] = fingerprint
        meta["model_discovery"] = {"status": "unknown", "source": None, "observed_at": None}
        name = cmd.names[index].strip() if index < len(cmd.names) else ""
        if name:
            meta["display_name"] = name
        status, result = (
            ("done", {})
            if previous
            else await execute_provider_command(
                pool,
                "add_provider",
                {
                    "provider_id": cmd.provider_id,
                    "kind": "ai",
                    "executor": "cli_agent",
                    "cli": cli,
                    "account_id": account_id,
                    "label": name
                    or f"{_NAMES.get(cmd.provider_id, cmd.provider_id.title())} ({email or account_id})",
                    "models": [],
                    "max_parallel": cmd.max_parallel,
                    "status": "needs_login",
                    "plan": {},
                    "meta": meta,
                },
                actor=actor,
            )
        )
        if status != "done":
            return "failed", {
                "error": f"Adding {account_id} failed: {result.get('error', status)}",
                "connection_ids": created,
                "session_ids": session_ids,
                "accounts": accounts,
            }
        created.append(account_id)
        taken.add(account_id)
        account: dict[str, Any] = {"connection_id": account_id, "session_id": None}
        accounts.append(account)
        if cli not in API_KEY_CLIS:  # an API-key account signs in by saving its key (save_secret)
            latest = await sessions_for_account(pool, account_id) if previous else None
            try:
                sid = latest or str(await service.start_login(tenant_id, account_id, actor))
                session_ids.append(sid)
                account["session_id"] = sid
            except Exception as exc:
                failures.append({"connection_id": account_id, "error": type(exc).__name__})
    return "failed" if failures else "done", {
        "connection_ids": created,
        "session_ids": session_ids,
        "accounts": accounts,
        "errors": failures,
    }


async def sessions_for_account(pool: DbPool, connection_id: str) -> str | None:
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select id from public.login_sessions where connection_id=%s order by created_at desc limit 1",
            (connection_id,),
        )
        row = await cur.fetchone()
    return str(row[0]) if row else None


def _usual_cli(provider_id: str, rows: list[tuple[str, dict[str, Any]]]) -> str:
    if provider_id == "gemini":
        return "gemini"
    counts = Counter(str(meta["cli"]) for _, meta in rows if meta.get("cli") in _CLIS)
    if counts:
        return counts.most_common(1)[0][0]
    if provider_id in _CLIS:
        return provider_id
    raise LoginError(f"'{provider_id}' is not an AI pool the Farm can sign accounts into")


async def _save_secret(
    pool: DbPool, tenant_id: UUID, cmd: SaveSecretPayload, actor: str
) -> tuple[str, dict[str, Any]]:
    if cmd.connection_id is not None:
        async with tenant_connection(pool) as conn:
            cur = await conn.execute("select id from public.connections where id=%s", (cmd.connection_id,))
            if await cur.fetchone() is None:
                return "rejected", {"error": f"There is no account '{cmd.connection_id}'."}
    value = get_login_service().open_input(cmd.sealed.model_dump(), f"{tenant_id}|{cmd.name}|secret")
    await asyncio.to_thread(set_secret, cmd.name, value)
    os.environ[cmd.name] = value  # env: references resolve from the process environment
    async with tenant_connection(pool) as conn, conn.transaction():
        if cmd.connection_id is not None:
            cur = await conn.execute(
                "update public.connections set auth_ref = %s where id = %s returning id",
                (f"env:{cmd.name}", cmd.connection_id),
            )
            if await cur.fetchone() is None:
                return "rejected", {"error": f"There is no account '{cmd.connection_id}'."}
        await write_audit(
            conn, actor, "save_secret", cmd.connection_id or cmd.name, {"name": cmd.name, "saved": True}
        )
    if cmd.connection_id is not None:
        from farm.registry.writer import write_registry_file

        await write_registry_file(pool)
    return "done", {"saved": cmd.name}


__all__ = [
    "CONNECT_PAYLOADS",
    "SEALED_KINDS",
    "AddAccountsPayload",
    "CancelLoginPayload",
    "SaveSecretPayload",
    "StartLoginPayload",
    "SubmitLoginInputPayload",
    "consumed_payload",
    "execute_connect_command",
]
