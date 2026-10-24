"""Dashboard commands for signing accounts in, handled by the LoginService (``farm_commands`` kinds).

``start_login``, ``submit_login_input``, ``cancel_login``, ``add_accounts`` and ``save_secret``. Pasted
codes and keys arrive sealed for this Farm (``farm.connect.sealed``); once such a command has run, the
command consumer stores ``"consumed"`` in place of the ciphertext (``consumed_payload``).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from farm.connect.sealed import SealedEnvelope, SealedError
from farm.connect.service import LoginError, get_login_service, write_audit
from farm.db.pool import DbPool, tenant_connection
from farm.secrets import set_secret
from farm.tenancy import require_tenant

_ID = r"^[a-z0-9][a-z0-9_-]{0,62}$"
_ENV_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"
_CLIS = ("claude", "codex", "gemini", "agy", "hermes")
_NAMES = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini", "hermes": "Hermes"}
_TEMPLATE_KEYS = ("allow_edit", "edit_roots", "timeout_s", "default_model")

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
    cli: Literal["claude", "codex", "gemini", "agy", "hermes"] | None = None
    plan: str | None = Field(default=None, max_length=40)
    emails: list[str] = Field(default_factory=list, max_length=10)
    max_parallel: int = Field(default=1, ge=1, le=4)

    @model_validator(mode="after")
    def _emails_fit(self) -> AddAccountsPayload:
        if len(self.emails) > self.count:
            raise ValueError("there are more emails than accounts")
        for email in self.emails:
            if email and "@" not in email:
                raise ValueError(f"'{email}' is not an email address")
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
    pool: DbPool, kind: str, payload: BaseModel, *, actor: str
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
            return await _add_accounts(pool, tenant_id, payload, actor)
        if isinstance(payload, SaveSecretPayload):
            return await _save_secret(pool, tenant_id, payload, actor)
    except (LoginError, SealedError) as exc:
        return "rejected", {"error": str(exc)}
    return "rejected", {"error": f"Unknown sign-in command '{kind}'"}


async def _add_accounts(
    pool: DbPool, tenant_id: UUID, cmd: AddAccountsPayload, actor: str
) -> tuple[str, dict[str, Any]]:
    # Imported here: the command module imports this one.
    from farm.control.commands import execute_provider_command

    service = get_login_service()  # refuse before creating accounts nobody could sign in
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select id, meta from public.connections where provider_id = %s order by id", (cmd.provider_id,)
        )
        rows = [(str(r[0]), r[1] if isinstance(r[1], dict) else {}) for r in await cur.fetchall()]
    cli = cmd.cli or _usual_cli(cmd.provider_id, rows)
    prefix = "agy-" if cli == "agy" else "gemini-cli-" if cli == "gemini" else f"{cmd.provider_id}-"
    template = next((meta for _, meta in rows if meta.get("cli") == cli), {})
    taken = {conn_id for conn_id, _ in rows}
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    number = max((int(m.group(1)) for c in taken if (m := pattern.fullmatch(c))), default=0) + 1

    created: list[str] = []
    session_ids: list[str] = []
    for index in range(cmd.count):
        while f"{prefix}{number:02d}" in taken:
            number += 1
        account_id = f"{prefix}{number:02d}"
        number += 1
        email = cmd.emails[index] if index < len(cmd.emails) and cmd.emails[index] else None
        meta: dict[str, Any] = {key: template[key] for key in _TEMPLATE_KEYS if key in template}
        if email:
            meta["expected_email"] = email
        status, result = await execute_provider_command(
            pool,
            "add_provider",
            {
                "provider_id": cmd.provider_id,
                "kind": "ai",
                "executor": "cli_agent",
                "cli": cli,
                "account_id": account_id,
                "label": f"{_NAMES.get(cmd.provider_id, cmd.provider_id.title())} ({email or account_id})",
                "models": [str(m) for m in template.get("models") or []],
                "max_parallel": cmd.max_parallel,
                "status": "needs_login",
                "plan": {"name": cmd.plan} if cmd.plan else {},
                "meta": meta,
            },
            actor=actor,
        )
        if status != "done":
            return "failed", {
                "error": f"Adding {account_id} failed: {result.get('error', status)}",
                "connection_ids": created,
                "session_ids": session_ids,
            }
        created.append(account_id)
        session_ids.append(str(await service.start_login(tenant_id, account_id, actor)))
    return "done", {"connection_ids": created, "session_ids": session_ids}


def _usual_cli(provider_id: str, rows: list[tuple[str, dict[str, Any]]]) -> str:
    counts = Counter(str(meta["cli"]) for _, meta in rows if meta.get("cli") in _CLIS)
    if counts:
        return counts.most_common(1)[0][0]
    if provider_id == "gemini":
        return "agy"
    if provider_id in _CLIS:
        return provider_id
    raise LoginError(f"'{provider_id}' is not an AI pool the Farm can sign accounts into")


async def _save_secret(
    pool: DbPool, tenant_id: UUID, cmd: SaveSecretPayload, actor: str
) -> tuple[str, dict[str, Any]]:
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

