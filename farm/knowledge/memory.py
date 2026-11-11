"""Exact project notes. Entries are data, never executable instructions or authority."""

from __future__ import annotations

import re
from typing import Any

from farm.db.pool import DbPool, tenant_connection
from farm.tenancy import current_user

PROJECT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}\Z")
MAX_BYTES = 16_384


def validate(project: str, key: str) -> None:
    if not PROJECT.fullmatch(project):
        raise ValueError("project must be a 1–63 character slug")
    if not 1 <= len(key) <= 128 or any(ord(c) < 32 for c in key):
        raise ValueError("key must contain 1–128 printable characters")


def note(row: tuple[Any, ...]) -> dict[str, Any]:
    return dict(zip(("key", "body", "version", "deleted", "updated_at"), row, strict=True))


async def get(pool: DbPool, project: str, key: str) -> dict[str, Any] | None:
    validate(project, key)
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select key, body, version, deleted, updated_at from public.project_memory "
            "where project=%s and key=%s",
            (project, key),
        )
        row = await cur.fetchone()
    return note(row) if row else None


async def list_notes(
    pool: DbPool, project: str, *, prefix: str = "", after: str = "", limit: int = 50
) -> dict[str, Any]:
    validate(project, "list")
    if not 1 <= limit <= 100 or len(prefix) > 128 or len(after) > 128:
        raise ValueError("limit must be 1–100 and prefix/cursor at most 128 characters")
    async with tenant_connection(pool) as conn:
        cur = await conn.execute(
            "select key, body, version, deleted, updated_at from public.project_memory "
            "where project=%s and not deleted and starts_with(key,%s) and key>%s order by key limit %s",
            (project, prefix, after, limit + 1),
        )
        rows = await cur.fetchall()
    return {
        "notes": [note(r) for r in rows[:limit]],
        "next": rows[limit - 1][0] if len(rows) > limit else None,
    }


async def put(
    pool: DbPool, project: str, key: str, body: str, expected_version: int, *, deleted: bool = False
) -> dict[str, Any]:
    validate(project, key)
    if expected_version < 0 or len(body.encode("utf-8")) > MAX_BYTES or "\x00" in body:
        raise ValueError("version must be nonnegative and note at most 16 KiB without NUL characters")
    async with tenant_connection(pool) as conn:
        if expected_version == 0:
            if deleted:
                raise ValueError("cannot delete a note that has no version")
            cur = await conn.execute(
                "insert into public.project_memory(project,key,body,updated_by) values(%s,%s,%s,%s) "
                "on conflict do nothing returning key,body,version,deleted,updated_at",
                (project, key, body, current_user.get()),
            )
        else:
            cur = await conn.execute(
                "update public.project_memory set body=%s, deleted=%s, version=version+1, "
                "updated_at=now(), updated_by=%s where project=%s and key=%s and version=%s "
                "returning key,body,version,deleted,updated_at",
                (body, deleted, current_user.get(), project, key, expected_version),
            )
        row = await cur.fetchone()
    if row is None:
        raise ValueError("memory version conflict; read the current note before updating")
    return note(row)
