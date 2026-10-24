"""Team members (SERVER1): a person of the team gets a Supabase sign-in and a membership of the tenant.

The Console signs people in with an e-mailed link and never creates accounts (``shouldCreateUser: false``),
and its row level security shows a tenant's rows only to a user with a ``memberships`` row (F1B).
``farm member add`` does both for one e-mail address:

1. the Supabase Auth user, through the Admin API with the service-role key: created already confirmed, so no
   e-mail goes out; an existing one is found and reused;
2. ``public.users`` (``auth_subject`` = the Auth user's id) and ``public.memberships`` for the tenant.

The member then asks the Console's login page for a link. ``farm token create <name>`` is the separate step
that lets their IDE call the Farm. Removing a member deletes the membership only (the sign-in then sees
nothing); their tokens are revoked with ``farm token revoke``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from farm.db.pool import DbPool, system_connection
from farm.tenancy.models import MILVOW_TENANT_ID, Role

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PAGE = 200


class MemberError(RuntimeError):
    """A member could not be added or removed; the message never holds a key."""


@dataclass(frozen=True)
class Member:
    email: str
    role: Role
    signed_up: bool
    """The Supabase Auth user is linked (``users.auth_subject`` is set)."""
    since: datetime


def check_email(email: str) -> str:
    email = email.strip()
    if not _EMAIL.fullmatch(email):
        raise MemberError(f"not an e-mail address: {email!r}")
    return email


def _reason(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    text = ""
    if isinstance(body, dict):
        text = str(body.get("msg") or body.get("error_description") or body.get("message") or "")
    return f"HTTP {response.status_code}" + (f": {text[:120]}" if text else "")


def ensure_auth_user(email: str, *, supabase_url: str, service_key: str) -> tuple[str, bool]:
    """The Supabase Auth user id of ``email`` and whether it was created now (confirmed, no e-mail sent)."""
    headers = {"apikey": service_key, "Authorization": f"Bearer {service_key}"}
    with httpx.Client(base_url=f"{supabase_url.rstrip('/')}/auth/v1", headers=headers, timeout=20.0) as http:
        created = http.post("/admin/users", json={"email": email, "email_confirm": True})
        if created.status_code in (200, 201):
            return str(created.json()["id"]), True
        if created.status_code not in (400, 409, 422):  # anything else is not "that user exists"
            raise MemberError(f"Supabase Auth did not create the user: {_reason(created)}")
        page = 1
        while True:
            listed = http.get("/admin/users", params={"page": page, "per_page": _PAGE})
            if listed.status_code != 200:
                raise MemberError(f"Supabase Auth did not list its users: {_reason(listed)}")
            users = listed.json().get("users") or []
            for user in users:
                if str(user.get("email", "")).lower() == email.lower():
                    return str(user["id"]), False
            if len(users) < _PAGE:
                raise MemberError(f"Supabase Auth refused {email} ({_reason(created)}) and has no such user")
            page += 1


async def add_member(
    pool: DbPool,
    email: str,
    role: Role,
    auth_subject: str,
    *,
    display_name: str | None = None,
    tenant_id: UUID = MILVOW_TENANT_ID,
) -> UUID:
    """Create or update the user row and the membership; returns the user's id."""
    async with system_connection(pool) as conn, conn.transaction():
        cur = await conn.execute(
            """
            insert into public.users (email, display_name, auth_subject) values (%s, %s, %s)
            on conflict ((lower(email))) do update
               set auth_subject = excluded.auth_subject,
                   display_name = coalesce(excluded.display_name, public.users.display_name)
            returning id
            """,
            (email, display_name, auth_subject),
        )
        row = await cur.fetchone()
        assert row is not None
        user_id: UUID = row[0]
        await conn.execute(
            """
            insert into public.memberships (tenant_id, user_id, role) values (%s, %s, %s)
            on conflict (tenant_id, user_id) do update set role = excluded.role
            """,
            (tenant_id, user_id, role.value),
        )
    return user_id


async def list_members(pool: DbPool, *, tenant_id: UUID = MILVOW_TENANT_ID) -> list[Member]:
    async with system_connection(pool) as conn:
        cur = await conn.execute(
            """
            select u.email, m.role, u.auth_subject is not null, m.created_at
            from public.memberships m join public.users u on u.id = m.user_id
            where m.tenant_id = %s
            order by m.created_at, u.email
            """,
            (tenant_id,),
        )
        rows = await cur.fetchall()
    return [Member(email=r[0], role=Role(r[1]), signed_up=bool(r[2]), since=r[3]) for r in rows]


async def remove_member(pool: DbPool, email: str, *, tenant_id: UUID = MILVOW_TENANT_ID) -> bool:
    """Delete the membership of ``email`` (True when there was one). The user row and sign-in stay."""
    async with system_connection(pool) as conn:
        cur = await conn.execute(
            """
            delete from public.memberships m using public.users u
            where m.user_id = u.id and m.tenant_id = %s and lower(u.email) = lower(%s)
            """,
            (tenant_id, email),
        )
        return cur.rowcount > 0


__all__ = [
    "Member",
    "MemberError",
    "add_member",
    "check_email",
    "ensure_auth_user",
    "list_members",
    "remove_member",
]
