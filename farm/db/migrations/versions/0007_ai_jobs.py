"""AI jobs and conversations (AIP2): non-blocking AI work, iterated on the same worker.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 00:00:04.000000

* ``ai_conversations``: one thread with one worker. It lives on exactly one account (the CLI session that
  holds the memory lives there), carries the CLI-native session id and the running totals of the thread.
* ``ai_jobs``: one turn of a conversation, run inside the Farm process. Only the trajectory is stored
  (state, account, model, tokens, cost, session id, attempts, error). The task text is never stored and the
  worker's answer lives in ``FARM_DATA_DIR/ai-results/<job id>.txt``, never in this table.
* ``owner_id`` / ``heartbeat_at``: the Farm process that owns a queued or running job renews the heartbeat;
  a job whose owner stopped renewing it is failed as ``farm_restart`` by any other process (no silent re-run).
* ``cancel_requested_at``: how the CLI (another process) asks the owner to stop a job.
* The unique partial index admits one queued/running job per conversation: two turns on one session at
  the same time would corrupt it.
* RLS is enabled; the owner-select policy follows 0002 and exists only where Supabase's ``auth`` schema does.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
create table public.ai_conversations (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  ai text not null,
  account text not null,
  native_session_id text,
  turns int not null default 0 check (turns >= 0),
  tokens bigint not null default 0 check (tokens >= 0),
  cost numeric(12,6) not null default 0 check (cost >= 0),
  last_job_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
comment on column public.ai_conversations.cost is 'USD, summed over the jobs of the conversation';

create table public.ai_jobs (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.ai_conversations (id) on delete cascade,
  turn int not null check (turn >= 1),
  ai text not null,
  account text not null,
  model text,
  mode text not null default 'answer' check (mode in ('answer', 'edit')),
  cwd text,
  state text not null default 'queued'
    check (state in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  timeout_s int not null default 900 check (timeout_s between 1 and 7200),
  retry_other_account boolean not null default false,
  caller text not null default 'mcp',
  task_chars int not null default 0 check (task_chars >= 0),
  json_schema jsonb,
  resume_session_id text,
  native_session_id text,
  owner_id uuid,
  heartbeat_at timestamptz,
  cancel_requested_at timestamptz,
  attempts jsonb not null default '[]'::jsonb,
  run_id uuid,
  result_path text,
  result_chars int check (result_chars is null or result_chars >= 0),
  result_sha256 text,
  json_valid boolean,
  json_errors jsonb,
  files_changed jsonb,
  usage jsonb not null default '{}'::jsonb,
  tokens bigint check (tokens is null or tokens >= 0),
  cost_usd numeric(12,6) not null default 0 check (cost_usd >= 0),
  cost_estimated boolean not null default true,
  duration_s numeric(12,3) check (duration_s is null or duration_s >= 0),
  error_kind text
    check (error_kind in ('limit', 'auth', 'timeout', 'crash', 'bad_request', 'cancelled', 'farm_restart',
                          'account_unavailable')),
  error_message text,
  error_cause text,
  retry_at timestamptz,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz not null default now(),
  check (state not in ('failed', 'cancelled') or error_kind is not null)
);
comment on column public.ai_jobs.cost_usd is 'USD; cost_estimated = the CLI reported no cost, this is the Farm estimate';

alter table public.ai_conversations
  add constraint fk_ai_conversations_last_job foreign key (last_job_id) references public.ai_jobs (id) on delete set null;

create unique index uq_ai_jobs_one_active_per_conversation on public.ai_jobs (conversation_id)
  where state in ('queued', 'running');
create index idx_ai_jobs_state_account on public.ai_jobs (state, account);
create index idx_ai_jobs_owner_active on public.ai_jobs (owner_id) where state in ('queued', 'running');
create index idx_ai_jobs_created_at on public.ai_jobs (created_at desc);
create index idx_ai_jobs_conversation_turn on public.ai_jobs (conversation_id, turn);
create index idx_ai_conversations_account on public.ai_conversations (account);
create index idx_ai_conversations_updated_at on public.ai_conversations (updated_at desc);

create trigger trg_ai_conversations_updated_at before update on public.ai_conversations
  for each row execute function public.set_updated_at();
create trigger trg_ai_jobs_updated_at before update on public.ai_jobs
  for each row execute function public.set_updated_at();

alter table public.ai_conversations enable row level security;
alter table public.ai_jobs enable row level security;

do $do$
declare
  t text;
begin
  -- Plain Postgres (local mode, tests) has no auth schema: no policies there. 0002 creates farm_is_owner()
  -- only where auth exists, so a database that gained auth later skips the policies instead of failing.
  if not exists (select 1 from pg_namespace where nspname = 'auth')
     or to_regprocedure('public.farm_is_owner()') is null then
    return;
  end if;
  foreach t in array array['ai_conversations', 'ai_jobs'] loop
    execute format('drop policy if exists %I on public.%I', 'owner_select_' || t, t);
    execute format(
      'create policy %I on public.%I for select to authenticated using ((select public.farm_is_owner()))',
      'owner_select_' || t, t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;
end;
$do$;
"""

DOWNGRADE_SQL = """
drop table if exists public.ai_jobs cascade;
drop table if exists public.ai_conversations cascade;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
