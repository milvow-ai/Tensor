"""Round-robin state, circuit open_seconds, and reactivate_due function.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04 00:00:04.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
-- 1. Pool state table for persistent round-robin cursors
create table if not exists public.pool_state (
  provider_id text primary key references public.providers(id) on delete cascade,
  rr_cursor int not null default 0,
  updated_at timestamptz not null default now()
);

alter table public.pool_state enable row level security;

do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'auth') then
    create policy "pool_state_owner_read" on public.pool_state for select
      using (auth.jwt()->>'email' = (select owner_email from public.farm_settings where id = 1));
  end if;
end $$;

-- 2. Add open_seconds to connection_health for backoff tracking
alter table public.connection_health
  add column if not exists open_seconds int null;

-- 3. SQL function to reactivate due exhausted connections
create or replace function public.farm_reactivate_due(p_now timestamptz default now())
returns int language plpgsql as $$
declare
  v_count int;
  v_debug text;
begin
  with due as (
    select c.id
    from public.connections c
    left join public.connection_health h on h.connection_id = c.id
    where c.status = 'exhausted'
      and (
        (h.cooldown_until is not null and h.cooldown_until <= p_now)
        or (h.cooldown_until is null)
      )
  ),
  reactivated as (
    update public.connections c
    set status = 'active'
    from due
    where c.id = due.id
    returning c.id
  )
  select count(*) into v_count from reactivated;

  -- Debug: show what we'd update
  select string_agg(connection_id::text || ':' || cooldown_until::text, ', ')
  into v_debug
  from connection_health
  where cooldown_until <= p_now;

  update public.connection_health
  set cooldown_until = null
  where connection_id in (
    select h.connection_id
    from public.connections c
    left join connection_health h on h.connection_id = c.id
    where c.status = 'active' and h.cooldown_until <= p_now
  );

  raise notice 'farm_reactivate_due: count=%, cleared=%', v_count, coalesce(v_debug, 'none');
  return v_count;
end;
$$;
"""

DOWNGRADE_SQL = """
drop function if exists public.farm_reactivate_due(timestamptz);
alter table public.connection_health drop column if exists open_seconds;
drop table if exists public.pool_state;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
