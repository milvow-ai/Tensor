"""Tenancy, enforce step (F1B): runtime roles, tenant defaults, row level security, hashed API tokens.

* Roles ``farm_app`` (runtime, no BYPASSRLS) and ``farm_system`` (BYPASSRLS, cross-tenant maintenance). Neither can
  log in: the Farm connects as before and ``SET ROLE``s into one per checkout (``farm.db.pool``), so no new
  credentials exist. Roles are cluster-wide, so the downgrade revokes their privileges here but keeps the roles.
* ``public.farm_tenant_id()`` reads ``app.tenant_id`` (NULL when unset or empty). Every tenant-owned column defaults
  to it, so an insert without a tenant fails NOT NULL instead of landing in another tenant; the policy
  ``tenant_isolation`` limits ``farm_app`` to the rows of that tenant, for reads and for writes.
* Supabase only (guarded by the ``auth`` schema): the Console policies stop checking the owner e-mail and become
  membership based: a user sees the rows of the tenants they belong to (``memberships`` x ``users.auth_subject``).
* Every view is ``security_invoker`` so the reader's RLS applies, not the view owner's.
* ``api_tokens`` replaces the runtime-created ``client_tokens`` (QUALITY D34): tenant, optional user, scopes, sha256
  hash only. Existing tokens move to tenant #1 and keep working; ``farm_heartbeats`` (platform table) joins the
  schema instead of being created at runtime.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-24 00:00:00.000000

``down_revision`` is 0018 (Q0G follow-ups), which recreates v_connection_status (already security_invoker) and farm_reactivate_due.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables without a tenant: everything else in schema public is tenant-owned.
PLATFORM_TABLES = "('alembic_version', 'tenants', 'users', 'farm_heartbeats')"
MILVOW = "00000000-0000-0000-0000-000000000001"

UPGRADE_SQL = (
    r"""
-- 1. roles ---------------------------------------------------------------------------------------------------
do $do$
begin
  begin
    create role farm_app nologin nobypassrls;
  exception when duplicate_object then null;
  end;
  begin
    create role farm_system nologin bypassrls;
  exception
    when duplicate_object then null;
    when insufficient_privilege then
      create role farm_system nologin;
      raise warning 'farm_system was created without BYPASSRLS (needs a superuser): maintenance will see no rows';
  end;
  -- A non-superuser (Supabase postgres) may only SET ROLE into roles it is a member of.
  if not (select rolsuper from pg_roles where rolname = current_user) then
    execute format('grant farm_app, farm_system to %I', current_user);
  end if;
end $do$;

create or replace function public.farm_tenant_id() returns uuid
  language sql stable parallel safe
  as $$ select nullif(current_setting('app.tenant_id', true), '')::uuid $$;

-- 2. platform tables ------------------------------------------------------------------------------------------
create table if not exists public.farm_heartbeats (
  component      text primary key,
  last_heartbeat timestamptz not null,
  pid            int,
  extra          jsonb
);
alter table public.farm_heartbeats enable row level security;

-- 3. API tokens (replaces the runtime client_tokens table) --------------------------------------------------
create table public.api_tokens (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     uuid not null default public.farm_tenant_id() references public.tenants(id),
  user_id       uuid references public.users(id) on delete set null,
  name          text not null,
  token_prefix  text not null,
  token_hash    text not null unique,
  scopes        text[] not null default '{*}',
  created_at    timestamptz not null default now(),
  last_used_at  timestamptz,
  revoked_at    timestamptz
);
create index api_tokens_tenant_id_idx on public.api_tokens (tenant_id);
alter table public.api_tokens enable row level security;

do $do$
begin
  if to_regclass('public.client_tokens') is not null then
    -- Tokens were stored as sha256 hex; anything else (a plain-text row) is hashed here.
    insert into public.api_tokens (tenant_id, name, token_prefix, token_hash, created_at, last_used_at)
    select '"""
    + MILVOW
    + r"""', client_name, 'farm_',
           case when token_hash ~ '^[0-9a-f]{64}$' then token_hash
                else encode(sha256(convert_to(btrim(token_hash), 'UTF8')), 'hex') end,
           created_at, last_used
      from public.client_tokens
    on conflict (token_hash) do nothing;
    drop table public.client_tokens;
  end if;
end $do$;

-- 4. defaults, policies and grants on every tenant-owned table -------------------------------------------------
grant usage on schema public to farm_app, farm_system;
grant execute on all functions in schema public to farm_app, farm_system;
grant usage, select on all sequences in schema public to farm_app, farm_system;
grant all on all tables in schema public to farm_system;
grant all on public.farm_heartbeats to farm_system;

do $do$
declare
  t record;
begin
  for t in
    select c.relname as name, c.relkind
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
      and c.relname not in """
    + PLATFORM_TABLES
    + r"""
      and exists (select 1 from pg_attribute a
                  where a.attrelid = c.oid and a.attname = 'tenant_id' and not a.attisdropped)
  loop
    execute format('alter table public.%I alter column tenant_id set default public.farm_tenant_id()', t.name);
    execute format('alter table public.%I enable row level security', t.name);
    execute format('drop policy if exists tenant_isolation on public.%I', t.name);
    execute format(
      'create policy tenant_isolation on public.%I for all to farm_app '
      'using (tenant_id = (select public.farm_tenant_id())) '
      'with check (tenant_id = (select public.farm_tenant_id()))', t.name);
    execute format('grant select, insert, update, delete on public.%I to farm_app', t.name);
  end loop;
end $do$;

-- Views read by the runtime and the Console apply the reader's RLS.
do $do$
declare
  v record;
begin
  for v in
    select c.relname as name from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind = 'v'
  loop
    execute format('alter view public.%I set (security_invoker = on)', v.name);
    execute format('grant select on public.%I to farm_app', v.name);
  end loop;
end $do$;

alter default privileges in schema public grant select, insert, update, delete on tables to farm_app;
alter default privileges in schema public grant all on tables to farm_system;
alter default privileges in schema public grant usage, select on sequences to farm_app, farm_system;
alter default privileges in schema public grant execute on functions to farm_app, farm_system;

-- 5. Supabase: Console policies become membership based -------------------------------------------------------
do $do$
declare
  p record;
begin
  if not exists (select 1 from pg_namespace where nspname = 'auth') then
    return;
  end if;

  -- The tenants of the signed-in user. SECURITY DEFINER: memberships/users have no Console policy, and a policy
  -- that selected them directly would see nothing.
  execute $f$
    create or replace function public.farm_member_tenant_ids() returns setof uuid
    language plpgsql stable security definer set search_path = ''
    as $b$
    begin
      return query
        select m.tenant_id from public.memberships m
        join public.users u on u.id = m.user_id
        where u.auth_subject = (select auth.uid())::text;
    end;
    $b$
  $f$;
  revoke all on function public.farm_member_tenant_ids() from public;
  grant execute on function public.farm_member_tenant_ids() to authenticated, farm_system;

  for p in
    select tablename as name from pg_policies
    where schemaname = 'public' and cmd = 'SELECT'
      and (policyname like 'owner\_select\_%' or policyname = 'pool_state_owner_read')
  loop
    execute format('drop policy if exists %I on public.%I', 'owner_select_' || p.name, p.name);
    execute format('drop policy if exists %I on public.%I', 'pool_state_owner_read', p.name);
    execute format('drop policy if exists %I on public.%I', 'member_select_' || p.name, p.name);
    execute format(
      'create policy %I on public.%I for select to authenticated '
      'using (tenant_id in (select public.farm_member_tenant_ids()))', 'member_select_' || p.name, p.name);
  end loop;

  drop policy if exists owner_insert_farm_commands on public.farm_commands;
  drop policy if exists member_insert_farm_commands on public.farm_commands;
  create policy member_insert_farm_commands on public.farm_commands for insert to authenticated
    with check (status = 'queued' and tenant_id in (select public.farm_member_tenant_ids()));

  -- The Console inserts commands without a tenant: a user with exactly one membership gets that tenant.
  execute $f$
    create or replace function public.farm_commands_console_tenant() returns trigger
    language plpgsql security definer set search_path = ''
    as $b$
    declare
      mine uuid[];
    begin
      if new.tenant_id is null then
        select array_agg(t) into mine from public.farm_member_tenant_ids() t;
        if array_length(mine, 1) = 1 then
          new.tenant_id := mine[1];
        end if;
      end if;
      return new;
    end;
    $b$
  $f$;
  revoke all on function public.farm_commands_console_tenant() from public;
  drop trigger if exists trg_farm_commands_console_tenant on public.farm_commands;
  create trigger trg_farm_commands_console_tenant before insert on public.farm_commands
    for each row execute function public.farm_commands_console_tenant();
end $do$;
"""
)

DOWNGRADE_SQL = (
    r"""
-- 5. Supabase policies back to the owner e-mail test
do $do$
declare
  p record;
begin
  if not exists (select 1 from pg_namespace where nspname = 'auth') then
    return;
  end if;
  drop trigger if exists trg_farm_commands_console_tenant on public.farm_commands;
  drop function if exists public.farm_commands_console_tenant();
  drop policy if exists member_insert_farm_commands on public.farm_commands;
  if to_regprocedure('public.farm_is_owner()') is not null then
    create policy owner_insert_farm_commands on public.farm_commands for insert to authenticated
      with check (status = 'queued' and (select public.farm_is_owner()));
  end if;
  for p in
    select tablename as name from pg_policies
    where schemaname = 'public' and cmd = 'SELECT' and policyname like 'member\_select\_%'
  loop
    execute format('drop policy if exists %I on public.%I', 'member_select_' || p.name, p.name);
    if to_regprocedure('public.farm_is_owner()') is not null then
      execute format(
        'create policy %I on public.%I for select to authenticated using ((select public.farm_is_owner()))',
        'owner_select_' || p.name, p.name);
    end if;
  end loop;
  drop function if exists public.farm_member_tenant_ids();
end $do$;

-- 4. defaults, policies, grants
alter default privileges in schema public revoke select, insert, update, delete on tables from farm_app;
alter default privileges in schema public revoke all on tables from farm_system;
alter default privileges in schema public revoke usage, select on sequences from farm_app, farm_system;
alter default privileges in schema public revoke execute on functions from farm_app, farm_system;

do $do$
declare
  t record;
begin
  for t in
    select c.relname as name
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
      and c.relname not in """
    + PLATFORM_TABLES
    + r"""
      and exists (select 1 from pg_attribute a
                  where a.attrelid = c.oid and a.attname = 'tenant_id' and not a.attisdropped)
  loop
    execute format('drop policy if exists tenant_isolation on public.%I', t.name);
    execute format('alter table public.%I alter column tenant_id set default %L', t.name, '"""
    + MILVOW
    + r"""');
  end loop;
end $do$;

revoke all on all tables in schema public from farm_app, farm_system;
revoke all on all sequences in schema public from farm_app, farm_system;
revoke execute on all functions in schema public from farm_app, farm_system;
revoke usage on schema public from farm_app, farm_system;

-- 3. API tokens back to the runtime table's shape
create table public.client_tokens (
  token_hash  text primary key,
  client_name text not null,
  created_at  timestamptz not null default now(),
  last_used   timestamptz
);
alter table public.client_tokens enable row level security;
insert into public.client_tokens (token_hash, client_name, created_at, last_used)
select token_hash, name, created_at, last_used_at from public.api_tokens
where tenant_id = '"""
    + MILVOW
    + r"""' and revoked_at is null;
drop table public.api_tokens;

drop table if exists public.farm_heartbeats;
drop function if exists public.farm_tenant_id();
"""
)


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
