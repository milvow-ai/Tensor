"""Project task ownership and durable step state. External work runs outside SQL transactions."""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from farm.db.pool import Conn, DbPool, tenant_connection
from farm.secrets import redact

Approach = Literal["finish", "delegate"]
StepKind = Literal["local", "cli", "mcp", "api"]
Resolution = Literal["completed", "superseded"]
TerminalState = Literal["succeeded", "failed", "paused", "outcome_unknown"]
LEASE = timedelta(minutes=3)
MAX_STEPS = 128
MAX_EVENTS = 100


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    user_id: UUID | None
    key: str
    client: str


@dataclass(frozen=True)
class Reservation:
    id: UUID
    fresh: bool


def workspace_path(value: str) -> str:
    """Match a local IDE's absolute path even when the Farm runs on another OS."""
    if len(value) > 1024 or any(ord(ch) < 32 for ch in value):
        raise TaskError("invalid workspace path")
    drive, rest = ntpath.splitdrive(value)
    if drive and rest.startswith(("\\", "/")):
        return ntpath.normpath(value).replace("\\", "/").casefold()
    if value.startswith("/"):
        return "/" + posixpath.normpath(value).lstrip("/")
    raise TaskError("workspace must be an absolute folder path")


def _text(value: str, size: int, *, empty: bool = False) -> str:
    value = redact(value.strip().replace("\x00", ""))
    if (not empty and not value) or len(value.encode("utf-8", errors="replace")) > size:
        raise TaskError(f"text must be within {size} UTF-8 bytes")
    return value


def _fingerprint(kind: str, target: str | None, arguments: dict[str, Any], write_root: str | None) -> str:
    try:
        raw = json.dumps([kind, target, arguments, write_root], sort_keys=True, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise TaskError("step arguments must be JSON") from None
    if len(raw) > 1_048_576:
        raise TaskError("step arguments exceed 1 MiB")
    return hashlib.sha256(raw).hexdigest()


STEP_ROWS = """
select s.id,s.step_key,s.kind,s.label,s.target,s.write_root,s.assigned_by,s.operator_epoch,
  case when s.resolution is not null then s.resolution
       when j.state='failed' and j.error_kind='outcome_unknown' then 'outcome_unknown'
       when j.state='failed' and j.error_kind in ('limit','auth','account_unavailable') then 'paused'
       when j.id is not null then j.state
       when s.state='running' and s.deadline<=%s then
          case when s.kind in ('mcp','api') then 'outcome_unknown' else 'paused' end
       when s.state='running' and s.kind='cli' then 'submitting'
       else s.state end as state,
  case when s.step_key=%s then s.receipt end as receipt,
  s.resolution_note,s.resolved_by,s.created_at,coalesce(j.finished_at,s.finished_at) as finished_at,
  j.id as job_id,coalesce(j.account,r.connection_id,r.active_connection_id) as account,
  j.model,j.resolved_model,j.error_kind,j.retry_at,coalesce(j.run_id,r.id) as run_id,
  s.fingerprint,s.deadline
from public.farm_task_steps s
left join public.ai_jobs j on j.tenant_id=s.tenant_id and j.id=s.id and s.kind='cli'
left join public.runs r on r.tenant_id=s.tenant_id and r.id=s.id and s.kind in ('api','mcp')
where s.task_id=%s and s.tenant_id=public.farm_tenant_id()
"""


class TaskStore:
    def __init__(self, pool: DbPool, clock: Callable[[], datetime]) -> None:
        self.pool, self.clock = pool, clock

    @asynccontextmanager
    async def _locked(self, task: UUID, actor: Actor) -> AsyncIterator[tuple[Conn, dict[str, Any]]]:
        async with tenant_connection(self.pool) as conn, conn.transaction():
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select * from public.farm_tasks where id=%s "
                    "and owner_id is not distinct from %s for update",
                    (task, actor.user_id),
                )
                row = await cur.fetchone()
            if row is None:
                raise TaskError("task not found")
            yield conn, row

    def _operator(self, row: dict[str, Any], actor: Actor, epoch: int) -> None:
        if row["state"] != "active" or row["lease_until"] <= self.clock():
            raise TaskError("task operator lease is inactive; take over the task before continuing")
        if row["operator_key"] != actor.key or row["epoch"] != epoch:
            raise TaskError("another operator owns this task; refresh its status")

    async def _event(
        self, conn: Conn, task: UUID, kind: str, actor: Actor, epoch: int, approach: str,
        previous: str | None = None,
    ) -> None:
        await conn.execute(
            "insert into public.farm_task_events(task_id,kind,actor,previous_actor,epoch,approach,at) "
            "values(%s,%s,%s,%s,%s,%s,%s)",
            (task, kind, actor.client, previous, epoch, approach, self.clock()),
        )

    async def _renew(self, conn: Conn, task: UUID) -> None:
        now = self.clock()
        await conn.execute(
            "update public.farm_tasks set lease_until=%s,updated_at=%s where id=%s",
            (now + LEASE, now, task),
        )

    async def create(
        self, actor: Actor, project: str, workspace: str, title: str, goal: str, approach: Approach,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", project):
            raise TaskError("invalid project name")
        task, now = uuid4(), self.clock()
        async with tenant_connection(self.pool) as conn, conn.transaction():
            await conn.execute(
                "insert into public.farm_tasks(id,owner_id,project,workspace,title,goal,"
                "operator_key,operator_client,approach,lease_until,created_at,updated_at) "
                "values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (task, actor.user_id, project, workspace_path(workspace), _text(title, 200),
                 _text(goal, 32768), actor.key, actor.client, approach, now + LEASE, now, now),
            )
            await self._event(conn, task, "opened", actor, 1, approach)
        return await self.get(actor, task)

    def _head(self, row: dict[str, Any]) -> dict[str, Any]:
        row.pop("operator_key", None)
        row.pop("owner_id", None)
        row.pop("tenant_id", None)
        expired = row["state"] == "active" and row["lease_until"] <= self.clock()
        if expired:
            row["state"] = "paused"
        row["operator_expired"] = expired
        return row

    async def _steps(self, conn: Conn, task: UUID, receipt_key: str | None = None) -> list[dict[str, Any]]:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                STEP_ROWS + " order by s.created_at,s.id limit 128", (self.clock(), receipt_key, task),
            )
            return await cur.fetchall()

    async def get(self, actor: Actor, task: UUID, *, step_key: str | None = None) -> dict[str, Any]:
        async with tenant_connection(self.pool) as conn, conn.transaction():
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "select * from public.farm_tasks where id=%s and owner_id is not distinct from %s",
                    (task, actor.user_id),
                )
                head = await cur.fetchone()
                if head is None:
                    raise TaskError("task not found")
                await cur.execute(
                    "select kind,actor,previous_actor,epoch,approach,at from public.farm_task_events "
                    "where task_id=%s order by id desc limit %s", (task, MAX_EVENTS),
                )
                events = await cur.fetchall()
            steps = await self._steps(conn, task, step_key)
        is_operator = head["operator_key"] == actor.key and head["state"] == "active"
        head = self._head(head)
        head["is_operator"] = is_operator and not head["operator_expired"]
        if step_key is not None:
            steps = [step for step in steps if step["step_key"] == step_key]
            if not steps:
                raise TaskError("step not found")
        for step in steps:
            step.pop("fingerprint", None)
            if step_key is None:
                step.pop("receipt", None)
        return {**head, "steps": steps, "operator_history": list(reversed(events))}

    async def list(self, actor: Actor, workspace: str, project: str | None) -> list[dict[str, Any]]:
        async with tenant_connection(self.pool) as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id,project,workspace,title,state,operator_client,approach,epoch,"
                "lease_until,updated_at "
                "from public.farm_tasks where owner_id is not distinct from %s and workspace=%s "
                "and (%s::text is null or project=%s) and state<>'done' order by updated_at desc limit 50",
                (actor.user_id, workspace_path(workspace), project, project),
            )
            return [self._head(row) for row in await cur.fetchall()]

    async def take_over(
        self, actor: Actor, task: UUID, epoch: int, approach: Approach, *, workspace: str,
        replace_active: bool = False, workspace_checked: bool = False,
    ) -> dict[str, Any]:
        async with self._locked(task, actor) as (conn, row):
            now = self.clock()
            if row["state"] == "done" or row["epoch"] != epoch or row["workspace"] != workspace_path(
                workspace
            ):
                raise TaskError("task, workspace or operator version changed; refresh before taking over")
            active = row["state"] == "active" and row["lease_until"] > now
            if active and row["operator_key"] != actor.key and not replace_active:
                raise TaskError("the current operator still holds the lease; explicitly confirm replacement")
            steps = await self._steps(conn, task)
            if any(s["kind"] == "local" and s["state"] in ("running", "paused") for s in steps):
                if not workspace_checked:
                    raise TaskError("verify the previous IDE stopped editing and inspect the workspace first")
                await conn.execute(
                    "update public.farm_task_steps set state='paused' "
                    "where task_id=%s and kind='local' and state='running'", (task,),
                )
            await conn.execute(
                "update public.farm_tasks set state='active',operator_key=%s,operator_client=%s,"
                "approach=%s,epoch=%s,lease_until=%s,updated_at=%s where id=%s",
                (actor.key, actor.client, approach, epoch + 1, now + LEASE, now, task),
            )
            await self._event(conn, task, "takeover", actor, epoch + 1, approach, row["operator_client"])
        return await self.get(actor, task)

    async def checkpoint(
        self, actor: Actor, task: UUID, epoch: int, note: str | None = None, *,
        pause: bool = False, finished_local_step: str | None = None,
    ) -> dict[str, Any]:
        async with self._locked(task, actor) as (conn, row):
            self._operator(row, actor, epoch)
            note = _text(note, 32768) if note is not None else None
            if (pause or finished_local_step is not None) and note is None:
                raise TaskError("save a checkpoint describing work done and what remains")
            if finished_local_step is not None:
                cursor = await conn.execute(
                    "update public.farm_task_steps set state='succeeded',finished_at=%s,receipt=%s "
                    "where task_id=%s and step_key=%s and kind='local' "
                    "and operator_epoch=%s and state='running'",
                    (self.clock(), Jsonb({"checkpoint": note}), task, finished_local_step, epoch),
                )
                if cursor.rowcount != 1:
                    raise TaskError("local step is not running under this operator")
            if pause:
                await conn.execute(
                    "update public.farm_task_steps set state='paused' where task_id=%s and kind='local' "
                    "and state='running'", (task,),
                )
                await self._event(conn, task, "paused", actor, epoch, row["approach"])
            await conn.execute(
                "update public.farm_tasks set checkpoint=coalesce(%s,checkpoint),state=%s where id=%s",
                (note, "paused" if pause else "active", task),
            )
            await self._renew(conn, task)
        return await self.get(actor, task)

    async def reserve(
        self, actor: Actor, task: UUID, epoch: int, key: str, kind: StepKind, label: str,
        target: str | None, args: dict[str, Any], *, write_root: str | None = None,
        deadline: datetime | None = None,
    ) -> Reservation:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}", key):
            raise TaskError("step key allows up to 100 letters, numbers, dots, underscores or hyphens")
        label = _text(label, 200)
        async with self._locked(task, actor) as (conn, row):
            self._operator(row, actor, epoch)
            if kind == "local":
                write_root = row["workspace"]
            signature = _fingerprint(kind, target, {"label": label, "args": args}, write_root)
            steps = await self._steps(conn, task)
            existing = next((step for step in steps if step["step_key"] == key), None)
            if existing and existing["fingerprint"] != signature:
                raise TaskError("step key is already used for different work; use a new key")
            if existing and not (kind == "local" and existing["state"] == "paused"):
                return Reservation(existing["id"], False)
            if kind == "cli" and row["approach"] != "delegate":
                raise TaskError("choose delegate when taking over before assigning an AI worker")
            if kind == "local" and row["approach"] != "finish":
                raise TaskError("choose finish when taking over before starting an IDE edit")
            if not existing and len(steps) >= MAX_STEPS:
                raise TaskError("task has reached its 128-step limit")
            if write_root is not None:
                await self._check_writer(conn, write_root, existing["id"] if existing else None)
            step_id = existing["id"] if existing else uuid4()
            if existing:
                await conn.execute(
                    "update public.farm_task_steps set state='running',assigned_by=%s,operator_epoch=%s "
                    "where id=%s", (actor.client, epoch, step_id),
                )
            else:
                await conn.execute(
                    "insert into public.farm_task_steps(id,task_id,step_key,fingerprint,kind,label,target,"
                    "write_root,operator_epoch,assigned_by,deadline,created_at) "
                    "values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (step_id, task, key, signature, kind, label, target, write_root, epoch,
                     actor.client, deadline, self.clock()),
                )
            await self._renew(conn, task)
        return Reservation(step_id, True)

    async def _check_writer(self, conn: Conn, root: str, except_id: UUID | None) -> None:
        # Serialize workspace reservations, including paths nested inside another worker's checkout.
        await conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(current_setting('app.tenant_id'), 3101))"
        )
        cursor = await conn.execute(
            "select s.write_root from public.farm_task_steps s left join public.ai_jobs j "
            "on j.tenant_id=s.tenant_id and j.id=s.id and s.kind='cli' "
            "where s.write_root is not null and s.id is distinct from %s and s.resolution is null "
            "and ((s.kind='local' and s.state in ('running','paused')) "
            "or (s.kind='cli' and (j.state<>'succeeded' or (j.id is null and s.state in "
            "('running','outcome_unknown')))))",
            (except_id,),
        )
        root = root.rstrip("/") + "/"
        for (other,) in await cursor.fetchall():
            path = other.rstrip("/") + "/"
            if root.startswith(path) or path.startswith(root):
                raise TaskError("another edit owns this workspace; finish it or choose a separate worktree")

    async def record(
        self, step: UUID, state: TerminalState, receipt: dict[str, Any],
    ) -> bool:
        async with tenant_connection(self.pool) as conn:
            updated = await conn.execute(
                "update public.farm_task_steps set state=%s,receipt=%s,finished_at=%s "
                "where id=%s and state='running'",
                (state, Jsonb(receipt), self.clock(), step),
            )
            if updated.rowcount == 1:
                return True
            existing = await conn.execute(
                "select 1 from public.farm_task_steps where id=%s and state=%s and receipt=%s",
                (step, state, Jsonb(receipt)),
            )
            return await existing.fetchone() is not None

    async def resolve(
        self, actor: Actor, task: UUID, epoch: int, key: str, resolution: Resolution, evidence: str,
    ) -> dict[str, Any]:
        evidence = _text(evidence, 32768)
        async with self._locked(task, actor) as (conn, row):
            self._operator(row, actor, epoch)
            item = next((s for s in await self._steps(conn, task) if s["step_key"] == key), None)
            if item is None or item["state"] not in ("failed", "paused", "outcome_unknown", "cancelled"):
                raise TaskError("only interrupted or failed steps can be reconciled")
            await conn.execute(
                "update public.farm_task_steps set resolution=%s,resolution_note=%s,resolved_by=%s "
                "where id=%s", (resolution, evidence, actor.client, item["id"]),
            )
            await self._renew(conn, task)
        return await self.get(actor, task, step_key=key)

    async def finish(self, actor: Actor, task: UUID, epoch: int, summary: str) -> dict[str, Any]:
        async with self._locked(task, actor) as (conn, row):
            self._operator(row, actor, epoch)
            steps = await self._steps(conn, task)
            if any(step["state"] not in ("succeeded", "completed", "superseded") for step in steps):
                raise TaskError("finish or reconcile the remaining steps before completing the task")
            await conn.execute(
                "update public.farm_tasks set state='done',checkpoint=%s,updated_at=%s where id=%s",
                (_text(summary, 32768), self.clock(), task),
            )
            await self._event(conn, task, "completed", actor, epoch, row["approach"])
        return await self.get(actor, task)
