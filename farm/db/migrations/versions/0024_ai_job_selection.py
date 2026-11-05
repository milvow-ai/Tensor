"""Explicit model/effort choice of AI jobs, and the Console's view of the jobs.

``ai_jobs.model`` / ``effort`` stay the *requested* choice. New: ``effective_effort`` (what the driver applies,
e.g. Codex caps xhigh/max at high), ``selection_source`` (explicit, account_defaults, inherited; null on older
jobs: unknown) and ``resolved_model`` (the exact model the worker reported; null = unknown). The choice is
written with the queued row, so it is durable before the job can run.

``v_ai_jobs`` is what the Console reads: security invoker, so the member-select policy of ``ai_jobs`` scopes it
to the reader's tenants, and ``tenant_id`` lets the server narrow that to the selected tenant. No task input,
JSON schema, working directory, caller, session id or error text.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = r"""
alter table public.ai_jobs
  add column effective_effort text
    check (effective_effort is null or effective_effort in ('low', 'medium', 'high', 'xhigh', 'max')),
  add column selection_source text
    check (selection_source is null or selection_source in ('explicit', 'account_defaults', 'inherited')),
  add column resolved_model text check (resolved_model is null or length(resolved_model) <= 200);

create or replace view public.v_ai_jobs with (security_invoker = true) as
  select id, tenant_id, conversation_id, turn, ai, account, model, resolved_model, effort as requested_effort,
         effective_effort, selection_source, mode, state, tokens, cost_usd, cost_estimated, duration_s,
         error_kind, created_at, started_at, finished_at, updated_at
    from public.ai_jobs;

grant select on public.v_ai_jobs to farm_app, farm_system;

do $do$
begin
  if exists (select 1 from pg_namespace where nspname = 'auth') then
    grant select on public.v_ai_jobs to authenticated;
  end if;
end $do$;
"""

DOWNGRADE_SQL = r"""
drop view if exists public.v_ai_jobs;
alter table public.ai_jobs
  drop column if exists resolved_model,
  drop column if exists selection_source,
  drop column if exists effective_effort;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
