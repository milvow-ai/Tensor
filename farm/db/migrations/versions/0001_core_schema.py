"""Core schema and quota ledger functions (every table in CONTEXT section 3, plus the atomic ledger).

All objects live in schema public. Money is numeric (USD), time is timestamptz (period boundaries are UTC).

Revision ID: 0001
Revises: None
Create Date: 2026-10-04 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_SQL = """
-- 1. Farm settings
create table public.farm_settings (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id int primary key default 1 check (id = 1),
  owner_email text,
  global_monthly_budget_usd numeric,
  alert_thresholds int[] not null default '{50,80,100}',
  timezone text not null default 'UTC',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Singleton row. owner_email stays null (matches nobody) until the registry sync / Console sets it.
insert into public.farm_settings (id) values (1) on conflict (id) do nothing;

-- 2. Providers
create table public.providers (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id text primary key,
  name text not null,
  kind text not null check (kind in ('tool','ai')),
  executor text not null check (executor in ('api','mcp','llm','cli_agent','agent','browser','local','human')),
  default_strategy text,
  enabled bool not null default true,
  config jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 3. Connections
create table public.connections (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id text primary key,
  provider_id text not null references public.providers(id) on delete cascade,
  label text,
  auth_ref text,
  scope text[] not null default '{internal}',
  priority int not null default 100,
  strategy text null,
  concurrency int not null default 1,
  rate_per_min int null,
  status text not null default 'active' check (status in ('active','paused','needs_login','exhausted','disabled')),
  plan jsonb not null default '{}'::jsonb,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 4. Consumption units
create table public.consumption_units (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  connection_id text not null references public.connections(id) on delete cascade,
  unit text not null,
  limit_value numeric null,
  period text not null check (period in ('minute','hour','day','week','month','rolling_5h','total','none')),
  reset_anchor int null,
  next_reset_at timestamptz null,
  charged_on text not null default 'attempt' check (charged_on in ('attempt','success','found')),
  unit_cost_usd numeric not null default 0,
  estimate_per_call numeric not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (connection_id, unit)
);

-- 5. Quota usage
create table public.quota_usage (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  connection_id text not null,
  unit text not null,
  period_start timestamptz not null,
  used numeric not null default 0,
  reserved numeric not null default 0,
  limit_value numeric null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (connection_id, unit, period_start),
  foreign key (connection_id) references public.connections(id) on delete cascade,
  check (used >= 0 and reserved >= 0)
);

-- 6. Quota reservations
create table public.quota_reservations (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  connection_id text not null,
  unit text not null,
  amount numeric not null,
  request_id uuid,
  period_start timestamptz not null,
  status text not null default 'reserved' check (status in ('reserved','committed','released','expired')),
  expires_at timestamptz not null,
  actual numeric null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (connection_id) references public.connections(id) on delete cascade,
  check (amount >= 0)
);

-- 7. Usage events
create table public.usage_events (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id bigint generated always as identity primary key,
  connection_id text not null references public.connections(id) on delete cascade,
  unit text not null,
  amount numeric not null,
  kind text not null check (kind in ('estimated','actual')),
  cost_usd numeric not null default 0,
  request_id uuid,
  run_id uuid,
  at timestamptz not null default now()
);

-- 8. Balance snapshots
create table public.balance_snapshots (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  connection_id text not null references public.connections(id) on delete cascade,
  unit text not null,
  remaining numeric not null,
  source text not null check (source in ('api','manual','agent','cli')),
  at timestamptz not null default now()
);

-- 9. Connection health
create table public.connection_health (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  connection_id text primary key references public.connections(id) on delete cascade,
  circuit text not null default 'closed' check (circuit in ('closed','open','half_open')),
  consecutive_failures int not null default 0,
  last_error_kind text,
  last_error text,
  last_error_at timestamptz,
  cooldown_until timestamptz null,
  success_count bigint not null default 0,
  failure_count bigint not null default 0,
  last_success_at timestamptz,
  latency_ms_p50 int null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 10. Budgets
create table public.budgets (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  scope text not null check (scope in ('global','provider','connection')),
  ref text null,
  monthly_usd numeric not null,
  hard_stop bool not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 11. Billing events
create table public.billing_events (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  connection_id text not null references public.connections(id) on delete cascade,
  kind text not null check (kind in ('charge','renewal','refund','credit_purchase')),
  amount_usd numeric not null,
  at timestamptz not null default now(),
  note text
);

-- 12. Alerts
create table public.alerts (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  kind text not null,
  severity text not null check (severity in ('info','warn','critical')),
  message text not null,
  ref text,
  created_at timestamptz not null default now(),
  acked_at timestamptz null
);

-- 13. Capabilities
create table public.capabilities (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  name text primary key,
  kind text not null check (kind in ('tool','ai')),
  description text not null default '',
  input_schema jsonb not null default '{}'::jsonb,
  output_schema jsonb not null default '{}'::jsonb,
  default_strategy text,
  cache_ttl_seconds int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 14. Capability routes
create table public.capability_routes (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  capability text not null references public.capabilities(name) on delete cascade,
  provider_id text not null references public.providers(id) on delete cascade,
  position int not null default 0,
  enabled bool not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (capability, provider_id)
);

-- 15. Capability requests
create table public.capability_requests (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  request_hash text not null,
  capability text not null,
  params jsonb not null default '{}'::jsonb,
  status text not null check (status in ('pending','running','succeeded','failed')),
  result jsonb,
  run_id uuid,
  created_at timestamptz not null default now(),
  expires_at timestamptz,
  unique (workspace_id, request_hash)
);

-- 16. Runs
create table public.runs (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  capability text not null,
  request_id uuid references public.capability_requests(id) on delete set null,
  caller text not null,
  strategy text,
  status text not null check (status in ('running','succeeded','failed','blocked')),
  cost_usd numeric not null default 0,
  cached bool not null default false,
  connection_id text references public.connections(id) on delete set null,
  error_kind text,
  error text,
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

-- 17. Run events
create table public.run_events (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id bigint generated always as identity primary key,
  run_id uuid not null references public.runs(id) on delete cascade,
  seq int not null,
  kind text not null check (kind in ('plan','cache_hit','single_flight_join','policy_block','candidate','skip','reserve','reserve_failed','execute','success','failure','fallback','commit','release','requeue')),
  connection_id text references public.connections(id) on delete set null,
  data jsonb not null default '{}'::jsonb,
  at timestamptz not null default now()
);

-- 18. Entities
create table public.entities (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  kind text not null,
  canonical_key text not null unique,
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 19. Facts
create table public.facts (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  entity_id uuid not null references public.entities(id) on delete cascade,
  attribute text not null,
  value jsonb not null,
  source_connection_id text references public.connections(id) on delete set null,
  observed_at timestamptz not null default now(),
  expires_at timestamptz,
  confidence numeric not null default 1.0,
  evidence_ids uuid[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- 20. Evidence
create table public.evidence (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  sha256 text not null,
  path text not null,
  url text,
  thumb_path text null,
  captured_at timestamptz not null default now(),
  tool_version text
);

-- 21. AI sessions
create table public.ai_sessions (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  session_id text primary key,
  connection_id text not null references public.connections(id) on delete cascade,
  ai text not null,
  model text,
  created_at timestamptz not null default now(),
  last_used_at timestamptz not null default now()
);

-- 22. Farm commands
create table public.farm_commands (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('pause','resume','set_priority','set_strategy','set_budget','add_connection','update_connection','remove_connection','set_route','test_connection','ack_alert')),
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (status in ('queued','running','done','rejected','failed')),
  result jsonb,
  created_by text,
  created_at timestamptz not null default now(),
  done_at timestamptz
);

-- 23. Audit events
create table public.audit_events (
  workspace_id uuid not null default '00000000-0000-0000-0000-000000000001',
  id bigint generated always as identity primary key,
  actor text not null,
  action text not null,
  target text not null,
  before jsonb,
  after jsonb,
  at timestamptz not null default now()
);

-- Indexes (primary keys and unique constraints already cover the rest)
create index idx_connections_provider on public.connections (provider_id);
create index idx_quota_reservations_expiry on public.quota_reservations (expires_at) where status = 'reserved';
create index idx_quota_reservations_request on public.quota_reservations (request_id);
create index idx_usage_events_conn_at on public.usage_events (connection_id, at);
create index idx_usage_events_run on public.usage_events (run_id);
create index idx_balance_snapshots_conn_at on public.balance_snapshots (connection_id, unit, at desc);
create index idx_billing_events_conn_at on public.billing_events (connection_id, at);
create index idx_alerts_open on public.alerts (created_at desc) where acked_at is null;
create index idx_capability_routes_cap_pos on public.capability_routes (capability, position);
create index idx_capability_requests_expires_at on public.capability_requests (expires_at);
create index idx_runs_started_at_desc on public.runs (started_at desc);
create index idx_runs_request on public.runs (request_id);
create index idx_runs_connection on public.runs (connection_id);
create index idx_run_events_run_id_seq on public.run_events (run_id, seq);
create index idx_facts_entity_attr on public.facts (entity_id, attribute);
create index idx_ai_sessions_connection on public.ai_sessions (connection_id);
create index idx_farm_commands_status_created_at on public.farm_commands (status, created_at);
create index idx_audit_events_at on public.audit_events (at desc);

-- updated_at trigger (every table that has an updated_at column)
create function public.set_updated_at() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger trg_farm_settings_updated_at before update on public.farm_settings for each row execute function public.set_updated_at();
create trigger trg_providers_updated_at before update on public.providers for each row execute function public.set_updated_at();
create trigger trg_connections_updated_at before update on public.connections for each row execute function public.set_updated_at();
create trigger trg_consumption_units_updated_at before update on public.consumption_units for each row execute function public.set_updated_at();
create trigger trg_quota_usage_updated_at before update on public.quota_usage for each row execute function public.set_updated_at();
create trigger trg_quota_reservations_updated_at before update on public.quota_reservations for each row execute function public.set_updated_at();
create trigger trg_connection_health_updated_at before update on public.connection_health for each row execute function public.set_updated_at();
create trigger trg_budgets_updated_at before update on public.budgets for each row execute function public.set_updated_at();
create trigger trg_capabilities_updated_at before update on public.capabilities for each row execute function public.set_updated_at();
create trigger trg_capability_routes_updated_at before update on public.capability_routes for each row execute function public.set_updated_at();
create trigger trg_entities_updated_at before update on public.entities for each row execute function public.set_updated_at();
create trigger trg_facts_updated_at before update on public.facts for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------------------------------------
-- Quota ledger. These functions are the only way to touch quota_usage / quota_reservations.
-- ---------------------------------------------------------------------------------------------------------

-- Start of the quota period that contains p_at. All boundaries are UTC.
--   minute/hour/day/week : truncated (week starts Monday)
--   month                : starts on day p_anchor (default 1), clamped to the month length
--                          (anchor 31 -> Feb 28/29, Apr 30, ...); before that day: the previous month's start
--   rolling_5h/total/none: fixed epoch, so a single quota_usage row
create function public.farm_period_start(p_period text, p_anchor int, p_at timestamptz)
returns timestamptz
language plpgsql
stable
set search_path = ''
as $$
declare
  v_at timestamp := coalesce(p_at, now()) at time zone 'UTC';
  v_anchor int := greatest(coalesce(p_anchor, 1), 1);
  v_month timestamp;
  v_start timestamp;
begin
  if p_period in ('rolling_5h', 'total', 'none') then
    return timestamptz '1970-01-01 00:00:00+00';
  elsif p_period = 'minute' then
    return date_trunc('minute', v_at) at time zone 'UTC';
  elsif p_period = 'hour' then
    return date_trunc('hour', v_at) at time zone 'UTC';
  elsif p_period = 'day' then
    return date_trunc('day', v_at) at time zone 'UTC';
  elsif p_period = 'week' then
    return date_trunc('week', v_at) at time zone 'UTC';
  elsif p_period = 'month' then
    v_month := date_trunc('month', v_at);
    v_start := v_month + make_interval(days =>
      least(v_anchor, extract(day from v_month + interval '1 month' - interval '1 day')::int) - 1);
    if v_at < v_start then
      v_month := v_month - interval '1 month';
      v_start := v_month + make_interval(days =>
        least(v_anchor, extract(day from v_month + interval '1 month' - interval '1 day')::int) - 1);
    end if;
    return v_start at time zone 'UTC';
  end if;
  raise exception using message = 'unknown period ' || coalesce(p_period, '<null>'), errcode = '22023';
end;
$$;

-- Reserve p_amount of p_unit on p_connection. Returns the reservation id, or null when
-- used + reserved + p_amount would exceed limit_value (null limit = unlimited).
-- Race-free: the upsert and the guarded UPDATE both lock the single quota_usage row of the current period,
-- and in READ COMMITTED the UPDATE re-checks its WHERE clause against the latest committed row version.
create function public.farm_reserve(
  p_connection text,
  p_unit text,
  p_amount numeric,
  p_request uuid default null,
  p_ttl_seconds int default 300
) returns uuid
language plpgsql
set search_path = ''
as $$
declare
  v_period text;
  v_anchor int;
  v_limit numeric;
  v_period_start timestamptz;
  v_id uuid;
begin
  if p_amount is null or p_amount < 0 then
    raise exception using message = 'invalid reserve amount', errcode = '22023';
  end if;

  select period, reset_anchor, limit_value
    into v_period, v_anchor, v_limit
    from public.consumption_units
   where connection_id = p_connection and unit = p_unit;
  if not found then
    raise exception using
      message = 'unknown unit ' || coalesce(p_unit, '<null>') || ' for connection ' || coalesce(p_connection, '<null>'),
      errcode = 'P0002';
  end if;

  v_period_start := public.farm_period_start(v_period, v_anchor, now());

  -- Current period row; a changed limit in consumption_units is copied over on the next reserve.
  insert into public.quota_usage (connection_id, unit, period_start, used, reserved, limit_value)
  values (p_connection, p_unit, v_period_start, 0, 0, v_limit)
  on conflict (connection_id, unit, period_start)
  do update set limit_value = excluded.limit_value
  where public.quota_usage.limit_value is distinct from excluded.limit_value;

  update public.quota_usage
     set reserved = reserved + p_amount
   where connection_id = p_connection
     and unit = p_unit
     and period_start = v_period_start
     and (limit_value is null or used + reserved + p_amount <= limit_value);
  if not found then
    return null;
  end if;

  insert into public.quota_reservations (connection_id, unit, amount, request_id, period_start, expires_at)
  values (p_connection, p_unit, p_amount, p_request, v_period_start,
          now() + make_interval(secs => greatest(coalesce(p_ttl_seconds, 300), 0)))
  returning id into v_id;
  return v_id;
end;
$$;

-- Settle a reservation: move its amount out of reserved and add p_actual (default: the reserved amount) to
-- used. p_actual = 0 when charged_on = 'success' and the call failed. Idempotent: only a reservation that is
-- still 'reserved' is touched, a second call (or a commit after release/expiry) is a no-op.
create function public.farm_commit(p_reservation uuid, p_actual numeric default null)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_res public.quota_reservations%rowtype;
  v_actual numeric;
begin
  if p_actual is not null and p_actual < 0 then
    raise exception using message = 'invalid actual amount', errcode = '22023';
  end if;

  select * into v_res from public.quota_reservations where id = p_reservation for update;
  if not found or v_res.status <> 'reserved' then
    return;
  end if;

  v_actual := coalesce(p_actual, v_res.amount);

  update public.quota_usage
     set reserved = greatest(reserved - v_res.amount, 0),
         used = used + v_actual
   where connection_id = v_res.connection_id
     and unit = v_res.unit
     and period_start = v_res.period_start;

  update public.quota_reservations
     set status = 'committed', actual = v_actual
   where id = v_res.id;
end;
$$;

-- Give a reservation back without charging anything. Idempotent (same rule as farm_commit).
create function public.farm_release(p_reservation uuid)
returns void
language plpgsql
set search_path = ''
as $$
declare
  v_res public.quota_reservations%rowtype;
begin
  select * into v_res from public.quota_reservations where id = p_reservation for update;
  if not found or v_res.status <> 'reserved' then
    return;
  end if;

  update public.quota_usage
     set reserved = greatest(reserved - v_res.amount, 0)
   where connection_id = v_res.connection_id
     and unit = v_res.unit
     and period_start = v_res.period_start;

  update public.quota_reservations set status = 'released' where id = v_res.id;
end;
$$;

-- Release every reservation that is still 'reserved' past its expires_at (status becomes 'expired').
-- Returns how many were expired. Reservations locked by a concurrent commit/release are skipped.
create function public.farm_expire_reservations()
returns int
language plpgsql
set search_path = ''
as $$
declare
  v_res public.quota_reservations%rowtype;
  v_count int := 0;
begin
  for v_res in
    select * from public.quota_reservations
     where status = 'reserved' and expires_at < now()
     order by id
       for update skip locked
  loop
    update public.quota_usage
       set reserved = greatest(reserved - v_res.amount, 0)
     where connection_id = v_res.connection_id
       and unit = v_res.unit
       and period_start = v_res.period_start;

    update public.quota_reservations set status = 'expired' where id = v_res.id;
    v_count := v_count + 1;
  end loop;
  return v_count;
end;
$$;
"""

DOWNGRADE_SQL = """
drop table if exists public.audit_events cascade;
drop table if exists public.farm_commands cascade;
drop table if exists public.ai_sessions cascade;
drop table if exists public.evidence cascade;
drop table if exists public.facts cascade;
drop table if exists public.entities cascade;
drop table if exists public.run_events cascade;
drop table if exists public.runs cascade;
drop table if exists public.capability_requests cascade;
drop table if exists public.capability_routes cascade;
drop table if exists public.capabilities cascade;
drop table if exists public.alerts cascade;
drop table if exists public.billing_events cascade;
drop table if exists public.budgets cascade;
drop table if exists public.connection_health cascade;
drop table if exists public.balance_snapshots cascade;
drop table if exists public.usage_events cascade;
drop table if exists public.quota_reservations cascade;
drop table if exists public.quota_usage cascade;
drop table if exists public.consumption_units cascade;
drop table if exists public.connections cascade;
drop table if exists public.providers cascade;
drop table if exists public.farm_settings cascade;

drop function if exists public.farm_expire_reservations();
drop function if exists public.farm_release(uuid);
drop function if exists public.farm_commit(uuid, numeric);
drop function if exists public.farm_reserve(text, text, numeric, uuid, int);
drop function if exists public.farm_period_start(text, int, timestamptz);
drop function if exists public.set_updated_at();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
