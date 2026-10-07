"""Integration requests table and command kind (GUIDE1b).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
create table public.integration_requests (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  requested_by  text not null default 'unknown',
  name          text not null,
  kind          text not null check (kind in ('mcp', 'cli', 'api', 'account', 'other')),
  purpose       text not null,
  context       text,
  urgency       text not null default 'soon' check (urgency in ('now', 'soon', 'later')),
  links         text[] not null default '{}',
  status        text not null default 'open' check (status in ('open', 'in_progress', 'done', 'declined')),
  owner_note    text,
  resolved_at   timestamptz
);

create index idx_integration_requests_status on public.integration_requests (status);
create index idx_integration_requests_created_at on public.integration_requests (created_at desc);
create index idx_integration_requests_name_kind on public.integration_requests (lower(name), kind);

alter table public.integration_requests enable row level security;

do $do$
begin
  if exists (select 1 from pg_namespace where nspname = 'auth') then
    drop policy if exists owner_select_integration_requests on public.integration_requests;
    create policy owner_select_integration_requests on public.integration_requests for select to authenticated
      using ((select public.farm_is_owner()));
    grant select on public.integration_requests to authenticated;
  end if;
end $do$;

alter table public.farm_commands
  drop constraint if exists farm_commands_kind_check;

alter table public.farm_commands
  add constraint farm_commands_kind_check check (kind in (
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
"""

DOWNGRADE_SQL = """
drop table if exists public.integration_requests cascade;

alter table public.farm_commands
  drop constraint if exists farm_commands_kind_check;

alter table public.farm_commands
  add constraint farm_commands_kind_check check (kind in (
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
    'remove_provider'
  ));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
