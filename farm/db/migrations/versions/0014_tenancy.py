"""Tenancy, expand step (F1A): tenants, users, memberships; workspace_id becomes tenant_id everywhere.

Milvow is tenant #1 with the id every existing row already carries. The defaults stay until F1B removes them
(once the code passes the tenant explicitly); RLS policies and database roles are not touched here.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every other table in schema public is tenant-owned.
PLATFORM_TABLES = "('alembic_version', 'tenants', 'users')"

UPGRADE_SQL = (
    """
create table public.tenants (
  id          uuid primary key default gen_random_uuid(),
  slug        text not null unique,
  name        text not null,
  cell_id     text not null default 'cell-1',
  plan        text not null default 'beta',
  status      text not null default 'active' check (status in ('active', 'suspended', 'deleted')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

insert into public.tenants (id, slug, name)
values ('00000000-0000-0000-0000-000000000001', 'milvow', 'Milvow');

create table public.users (
  id            uuid primary key default gen_random_uuid(),
  email         text not null,
  display_name  text,
  auth_subject  text unique,
  locale        text not null default 'en',
  timezone      text not null default 'UTC',
  created_at    timestamptz not null default now(),
  last_seen_at  timestamptz
);

create unique index users_email_lower_key on public.users (lower(email));

create table public.memberships (
  tenant_id   uuid not null references public.tenants(id) on delete cascade,
  user_id     uuid not null references public.users(id) on delete cascade,
  role        text not null check (role in ('owner', 'admin', 'member')),
  created_at  timestamptz not null default now(),
  primary key (tenant_id, user_id)
);

create index memberships_user_id_idx on public.memberships (user_id);

-- No policies yet (F1B): with row level security on and none defined, the Data API roles see nothing.
alter table public.tenants enable row level security;
alter table public.users enable row level security;
alter table public.memberships enable row level security;

do $do$
declare
  t record;
begin
  -- 1. workspace_id -> tenant_id wherever it exists
  for t in
    select c.table_name from information_schema.columns c
    join information_schema.tables x on x.table_schema = c.table_schema and x.table_name = c.table_name
    where c.table_schema = 'public' and c.column_name = 'workspace_id' and x.table_type = 'BASE TABLE'
  loop
    execute format('alter table public.%I rename column workspace_id to tenant_id', t.table_name);
  end loop;

  -- 2. tenant_id on every other tenant-owned table. The default stays until F1B removes it, once the code
  --    passes the tenant explicitly.
  for t in
    select x.table_name from information_schema.tables x
    where x.table_schema = 'public' and x.table_type = 'BASE TABLE'
      and x.table_name not in """
    + PLATFORM_TABLES
    + """
      and not exists (
        select 1 from information_schema.columns c
        where c.table_schema = 'public' and c.table_name = x.table_name and c.column_name = 'tenant_id')
  loop
    execute format(
      'alter table public.%I add column tenant_id uuid not null default %L',
      t.table_name, '00000000-0000-0000-0000-000000000001');
  end loop;

  -- 3. FK to tenants, and a tenant-first index where no index leads with tenant_id
  for t in
    select c.table_name from information_schema.columns c
    join information_schema.tables x on x.table_schema = c.table_schema and x.table_name = c.table_name
    where c.table_schema = 'public' and c.column_name = 'tenant_id' and x.table_type = 'BASE TABLE'
      and c.table_name not in """
    + PLATFORM_TABLES
    + """
      and c.table_name <> 'memberships'
  loop
    execute format(
      'alter table public.%I add constraint %I foreign key (tenant_id) references public.tenants(id)',
      t.table_name, t.table_name || '_tenant_id_fkey');
    if not exists (
      select 1 from pg_index i
      join pg_attribute a on a.attrelid = i.indrelid and a.attnum = i.indkey[0]
      where i.indrelid = format('public.%I', t.table_name)::regclass and a.attname = 'tenant_id'
    ) then
      execute format('create index %I on public.%I (tenant_id)', t.table_name || '_tenant_id_idx', t.table_name);
    end if;
  end loop;
end $do$;
"""
)

# Tables that had no workspace_id before this revision (their tenant_id column is dropped again on downgrade).
ADDED_TABLES = ("pool_state", "integration_requests")

DOWNGRADE_SQL = (
    """
do $do$
declare
  t record;
begin
  for t in
    select c.table_name from information_schema.columns c
    join information_schema.tables x on x.table_schema = c.table_schema and x.table_name = c.table_name
    where c.table_schema = 'public' and c.column_name = 'tenant_id' and x.table_type = 'BASE TABLE'
      and c.table_name not in """
    + PLATFORM_TABLES
    + """
      and c.table_name <> 'memberships'
  loop
    execute format('alter table public.%I drop constraint if exists %I', t.table_name, t.table_name || '_tenant_id_fkey');
    execute format('drop index if exists public.%I', t.table_name || '_tenant_id_idx');
    if t.table_name = any (%L::text[]) then
      execute format('alter table public.%I drop column tenant_id', t.table_name);
    else
      execute format('alter table public.%I rename column tenant_id to workspace_id', t.table_name);
    end if;
  end loop;
end $do$;

drop table if exists public.memberships;
drop table if exists public.users;
drop table if exists public.tenants;
"""
)


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    # %L is expanded by Python here so the added-table list lives next to the upgrade logic.
    added = "{" + ",".join(ADDED_TABLES) + "}"
    op.execute(DOWNGRADE_SQL.replace("%L::text[]", f"'{added}'::text[]"))
