"""Connect core: login_sessions table, v_login_sessions view, and farm_settings.connect_public_key.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = r"""
-- 1. login_sessions table -------------------------------------------------------------------------------------
create table public.login_sessions (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         uuid not null default public.farm_tenant_id() references public.tenants(id),
  connection_id     text not null references public.connections(id) on delete cascade,
  provider_id       text not null,
  method            text not null check (method in ('device_code', 'paste_code', 'loopback', 'oauth_redirect', 'api_key', 'token')),
  state             text not null default 'queued' check (state in ('queued', 'starting', 'waiting_for_user', 'verifying', 'done', 'failed', 'expired', 'cancelled', 'duplicate', 'needs_verification')),
  auth_url          text,
  user_code         text,
  verification_uri  text,
  needs_input       boolean not null default false,
  input_hint        text,
  expires_at        timestamptz,
  identity          jsonb not null default '{}'::jsonb,
  evidence          jsonb not null default '{}'::jsonb,
  error_kind        text,
  error_excerpt     text,
  created_by        text not null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  finished_at       timestamptz
);

create index login_sessions_tenant_id_idx on public.login_sessions (tenant_id);

create unique index login_sessions_active_idx on public.login_sessions (tenant_id, connection_id)
  where state in ('queued', 'starting', 'waiting_for_user', 'verifying');

create trigger trg_login_sessions_updated_at before update on public.login_sessions
  for each row execute function public.set_updated_at();

-- RLS policies and grants for runtime roles exactly like 0019
alter table public.login_sessions enable row level security;
drop policy if exists tenant_isolation on public.login_sessions;
create policy tenant_isolation on public.login_sessions for all to farm_app
  using (tenant_id = (select public.farm_tenant_id()))
  with check (tenant_id = (select public.farm_tenant_id()));

grant select, insert, update, delete on public.login_sessions to farm_app;
grant all on public.login_sessions to farm_system;

do $do$
begin
  if exists (select 1 from pg_namespace where nspname = 'auth') then
    drop policy if exists member_select_login_sessions on public.login_sessions;
    create policy member_select_login_sessions on public.login_sessions for select to authenticated
      using (tenant_id in (select public.farm_member_tenant_ids()));
    grant select on public.login_sessions to authenticated;
  end if;
end $do$;

-- 2. View public.v_login_sessions -----------------------------------------------------------------------------
create or replace view public.v_login_sessions with (security_invoker = true) as
  -- no tenant_id: row level security already scopes the rows to the reader's tenant
  select id, connection_id, provider_id, method, state, auth_url, user_code, verification_uri, needs_input,
         input_hint, expires_at, identity, evidence, error_kind, error_excerpt, created_by, created_at,
         updated_at, finished_at
    from public.login_sessions;

grant select on public.v_login_sessions to farm_app, farm_system;

do $do$
begin
  if exists (select 1 from pg_namespace where nspname = 'auth') then
    grant select on public.v_login_sessions to authenticated;
  end if;
end $do$;

-- 3. Add connect_public_key column to public.farm_settings ---------------------------------------------------
alter table public.farm_settings add column if not exists connect_public_key jsonb;

-- 4. The dashboard's sign-in command kinds -------------------------------------------------------------------
alter table public.farm_commands drop constraint if exists farm_commands_kind_check;
alter table public.farm_commands add constraint farm_commands_kind_check check (kind in (
    'pause',
    'resume',
    'set_priority',
    'set_strategy',
    'set_budget',
    'add_connection',
    'update_connection',
    'remove_connection',
    'set_route',
    'test_connection',
    'ack_alert',
    'cancel_ai_job',
    'set_max_parallel',
    'set_mcp_tool_access',
    'sync_mcp_tools',
    'add_provider',
    'update_provider',
    'remove_provider',
    'resolve_integration_request',
    'start_login',
    'submit_login_input',
    'cancel_login',
    'add_accounts',
    'save_secret'
  ));
"""

DOWNGRADE_SQL = r"""
-- 4. Back to the command kinds of 0013 (queued or finished sign-in commands go: they cannot exist there)
delete from public.farm_commands where kind in ('start_login', 'submit_login_input', 'cancel_login', 'add_accounts', 'save_secret');
alter table public.farm_commands drop constraint if exists farm_commands_kind_check;
alter table public.farm_commands add constraint farm_commands_kind_check check (kind in (
    'pause',
    'resume',
    'set_priority',
    'set_strategy',
    'set_budget',
    'add_connection',
    'update_connection',
    'remove_connection',
    'set_route',
    'test_connection',
    'ack_alert',
    'cancel_ai_job',
    'set_max_parallel',
    'set_mcp_tool_access',
    'sync_mcp_tools',
    'add_provider',
    'update_provider',
    'remove_provider',
    'resolve_integration_request'
  ));

-- 3. Remove connect_public_key column from farm_settings
alter table public.farm_settings drop column if exists connect_public_key;

-- 2. Drop view v_login_sessions
drop view if exists public.v_login_sessions cascade;

-- 1. Drop table login_sessions
drop table if exists public.login_sessions cascade;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
