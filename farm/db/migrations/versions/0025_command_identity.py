"""Bind Console commands to authenticated administrators instead of caller-supplied labels.

Existing queued commands have no authenticated creator; reject them rather than grandfather a forged label.
Drain and stop the service before migrating. Local CLI/system commands remain explicitly trusted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = r"""
-- A deleted person must never turn a member token into an unbound service token.
alter table public.api_tokens drop constraint api_tokens_user_id_fkey;
alter table public.api_tokens add constraint api_tokens_user_id_fkey
  foreign key (user_id) references public.users(id) on delete cascade;

create function public.farm_revoke_removed_member_tokens() returns trigger
language plpgsql security definer set search_path = '' as $body$
begin
  update public.api_tokens set revoked_at = now()
    where tenant_id = old.tenant_id and user_id = old.user_id and revoked_at is null;
  return old;
end;
$body$;
revoke all on function public.farm_revoke_removed_member_tokens() from public;
create trigger trg_membership_revoke_tokens after delete on public.memberships
  for each row execute function public.farm_revoke_removed_member_tokens();

alter table public.farm_commands
  add column created_by_user_id uuid references public.users(id),
  add column requires_actor_authorization boolean not null default false;

update public.farm_commands set status = 'rejected', done_at = now(),
  result = '{"error":"Re-submit command after the authenticated command identity upgrade"}'::jsonb
  where status = 'queued';

do $do$
begin
  if not exists (select 1 from pg_namespace where nspname = 'auth') then return; end if;
  execute $f$
    create or replace function public.farm_admin_tenant_ids() returns setof uuid
    language sql stable security definer set search_path = '' as $b$
      select m.tenant_id from public.memberships m join public.users u on u.id = m.user_id
       join public.tenants t on t.id = m.tenant_id
       where u.auth_subject = (select auth.uid())::text
         and m.role in ('owner', 'admin') and t.status = 'active';
    $b$
  $f$;
  revoke all on function public.farm_admin_tenant_ids() from public;
  grant execute on function public.farm_admin_tenant_ids() to authenticated, farm_system;

  execute $f$
    create or replace function public.farm_commands_authenticated_actor() returns trigger
    language plpgsql security definer set search_path = '' as $b$
    declare actor record;
    begin
      if auth.uid() is not null or current_setting('role', true) in ('authenticated', 'anon') then
        select u.id, u.email into actor from public.users u
          join public.memberships m on m.user_id = u.id
          join public.tenants t on t.id = m.tenant_id
          where u.auth_subject = auth.uid()::text and m.tenant_id = new.tenant_id
            and m.role in ('owner', 'admin') and t.status = 'active';
        if not found then
          raise exception 'Only a current owner or administrator can queue management commands'
            using errcode = '42501';
        end if;
        new.created_by_user_id := actor.id;
        new.created_by := actor.email;
        new.requires_actor_authorization := true;
      end if;
      return new;
    end;
    $b$
  $f$;
  revoke all on function public.farm_commands_authenticated_actor() from public;
  create trigger trg_farm_commands_identity before insert on public.farm_commands
    for each row execute function public.farm_commands_authenticated_actor();

  drop policy if exists member_insert_farm_commands on public.farm_commands;
  create policy member_insert_farm_commands on public.farm_commands for insert to authenticated
    with check (status = 'queued' and requires_actor_authorization
      and created_by_user_id is not null
      and tenant_id in (select public.farm_admin_tenant_ids()));
end $do$;
"""

DOWNGRADE_SQL = r"""
drop trigger if exists trg_membership_revoke_tokens on public.memberships;
drop function if exists public.farm_revoke_removed_member_tokens();
alter table public.api_tokens drop constraint api_tokens_user_id_fkey;
alter table public.api_tokens add constraint api_tokens_user_id_fkey
  foreign key (user_id) references public.users(id) on delete set null;
drop trigger if exists trg_farm_commands_identity on public.farm_commands;
drop function if exists public.farm_commands_authenticated_actor();
do $do$
begin
  if exists (select 1 from pg_namespace where nspname = 'auth') then
    drop policy if exists member_insert_farm_commands on public.farm_commands;
    create policy member_insert_farm_commands on public.farm_commands for insert to authenticated
      with check (status = 'queued' and tenant_id in (select public.farm_member_tenant_ids()));
  end if;
end $do$;
drop function if exists public.farm_admin_tenant_ids();
alter table public.farm_commands drop column requires_actor_authorization, drop column created_by_user_id;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
