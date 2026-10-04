"""Row level security on every table, plus owner policies when running on Supabase.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 00:00:01.000000

* RLS is enabled on every table (including alembic_version) everywhere, local Postgres included. The Farm
  process connects as the table owner (Supabase: the postgres role), which bypasses RLS.
* Policies reference ``auth.*`` and the ``authenticated`` role, so they are created only inside a DO block
  guarded by ``exists (select 1 from pg_namespace where nspname = 'auth')``. Plain Postgres (local mode,
  tests) never sees them.
* Policy: the authenticated user whose ``auth.jwt()->>'email'`` equals ``farm_settings.owner_email`` may
  select every table and insert queued rows into ``farm_commands``. Nothing else (no update/delete).
* The owner test lives in ``public.farm_is_owner()`` (SECURITY DEFINER) so the policy on ``farm_settings``
  can read ``farm_settings`` without recursing into its own policy. A null owner_email matches nobody.
* On Supabase the farm_* functions are revoked from anon/authenticated so they cannot be called over RPC.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = [
    "farm_settings",
    "providers",
    "connections",
    "consumption_units",
    "quota_usage",
    "quota_reservations",
    "usage_events",
    "balance_snapshots",
    "connection_health",
    "budgets",
    "billing_events",
    "alerts",
    "capabilities",
    "capability_routes",
    "capability_requests",
    "runs",
    "run_events",
    "entities",
    "facts",
    "evidence",
    "ai_sessions",
    "farm_commands",
    "audit_events",
]
RLS_TABLES = [*TABLES, "alembic_version"]  # alembic_version: RLS on, no policy (owner only)

_ARRAY = ", ".join(f"'{t}'" for t in TABLES)

UPGRADE_SQL = (
    "".join(f"alter table public.{t} enable row level security;\n" for t in RLS_TABLES)
    + f"""
do $do$
declare
  t text;
  tables text[] := array[{_ARRAY}];
begin
  if not exists (select 1 from pg_namespace where nspname = 'auth') then
    return;
  end if;

  -- plpgsql bodies are not validated at creation time, so this works even before auth.jwt() is resolvable.
  execute $f$
    create or replace function public.farm_is_owner() returns boolean
    language plpgsql stable security definer set search_path = ''
    as $b$
    begin
      return coalesce(
        lower(auth.jwt() ->> 'email') = lower((select s.owner_email from public.farm_settings s where s.id = 1)),
        false);
    end;
    $b$
  $f$;
  revoke all on function public.farm_is_owner() from public;

  foreach t in array tables loop
    execute format('drop policy if exists %I on public.%I', 'owner_select_' || t, t);
    execute format(
      'create policy %I on public.%I for select to authenticated using ((select public.farm_is_owner()))',
      'owner_select_' || t, t);
    execute format('grant select on public.%I to authenticated', t);
  end loop;

  drop policy if exists owner_insert_farm_commands on public.farm_commands;
  create policy owner_insert_farm_commands on public.farm_commands for insert to authenticated
    with check (status = 'queued' and (select public.farm_is_owner()));
  grant insert on public.farm_commands to authenticated;

  grant execute on function public.farm_is_owner() to authenticated;
  grant usage on schema public to authenticated;

  -- Quota functions are for the Farm process (table owner) only, never for the Data API.
  revoke all on function public.farm_period_start(text, int, timestamptz) from public;
  revoke all on function public.farm_reserve(text, text, numeric, uuid, int) from public;
  revoke all on function public.farm_commit(uuid, numeric) from public;
  revoke all on function public.farm_release(uuid) from public;
  revoke all on function public.farm_expire_reservations() from public;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function public.farm_period_start(text, int, timestamptz) from anon;
    revoke all on function public.farm_reserve(text, text, numeric, uuid, int) from anon;
    revoke all on function public.farm_commit(uuid, numeric) from anon;
    revoke all on function public.farm_release(uuid) from anon;
    revoke all on function public.farm_expire_reservations() from anon;
  end if;
  revoke all on function public.farm_period_start(text, int, timestamptz) from authenticated;
  revoke all on function public.farm_reserve(text, text, numeric, uuid, int) from authenticated;
  revoke all on function public.farm_commit(uuid, numeric) from authenticated;
  revoke all on function public.farm_release(uuid) from authenticated;
  revoke all on function public.farm_expire_reservations() from authenticated;
end;
$do$;
"""
)

DOWNGRADE_SQL = (
    "".join(f"drop policy if exists owner_select_{t} on public.{t};\n" for t in TABLES)
    + "drop policy if exists owner_insert_farm_commands on public.farm_commands;\n"
    + "drop function if exists public.farm_is_owner();\n"
    + "".join(f"alter table public.{t} disable row level security;\n" for t in RLS_TABLES)
)


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
