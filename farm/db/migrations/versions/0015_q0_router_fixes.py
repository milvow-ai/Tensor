"""Q0D router / ledger fixes: rolling window, late commit, budget holds, recent outcomes, clean reactivation.

* ``connection_health.recent_outcomes``: the last 50 outcomes of an account (``S`` / ``F``), the window the
  ``failover`` ranking breaks ties with (Q0-08).
* ``quota_reservations.late``: the reservation had expired when its call finished and was charged afterwards (Q0-13).
* ``farm_quota_consumed`` / ``farm_reserve``: ``rolling_5h`` is a real sliding window over committed and held
  reservations of the last 5 hours instead of a lifetime counter (Q0-14).
* ``farm_commit``: committing an expired reservation records the usage (``late``) instead of dropping it (Q0-13).
* ``budget_reservations`` / ``farm_budget_reserve``: spend of an in-flight paid call is held against the hard-stop
  budgets under one lock, so concurrent calls cannot overshoot (Q0-04).
* ``farm_reactivate_due``: the debug ``string_agg`` / ``RAISE NOTICE`` is gone; a reset window clears the stale
  ``next_reset_at`` of the connection's units (Q0-23, Q0-05).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24 00:00:15.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_FUNCTIONS = (
    "public.farm_quota_consumed(text, text, timestamptz)",
    "public.farm_budget_reserve(text, numeric, uuid, timestamptz, timestamptz, int)",
)

# Server-only, like the other data-changing farm_* functions (0002): never callable over the Data API.
REVOKE_SQL = "".join(
    f"""
do $do$
begin
  revoke all on function {fn} from public;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function {fn} from anon;
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on function {fn} from authenticated;
  end if;
end $do$;
"""
    for fn in NEW_FUNCTIONS
)

UPGRADE_SQL = (
    """
alter table public.connection_health add column recent_outcomes text not null default '';
alter table public.quota_reservations add column late boolean not null default false;

-- ---------------------------------------------------------------------------------------------------------
-- Budget holds (Q0-04): the spend a paid call may cost, reserved before the call and dropped when it settled.
-- ---------------------------------------------------------------------------------------------------------
create table public.budget_reservations (
  tenant_id     uuid not null default '00000000-0000-0000-0000-000000000001' references public.tenants(id),
  id            uuid primary key default gen_random_uuid(),
  connection_id text not null references public.connections(id) on delete cascade,
  amount_usd    numeric not null check (amount_usd >= 0),
  request_id    uuid,
  expires_at    timestamptz not null,
  created_at    timestamptz not null default now()
);
create index budget_reservations_tenant_id_idx on public.budget_reservations (tenant_id, expires_at);
create index budget_reservations_connection_idx on public.budget_reservations (connection_id);
alter table public.budget_reservations enable row level security;

-- Quota consumed (used + reserved) of one unit in the period that contains p_at. rolling_5h is the sum of the
-- reservations held or committed in the 5 hours before p_at.
create function public.farm_quota_consumed(p_connection text, p_unit text, p_at timestamptz)
returns numeric
language plpgsql
stable
set search_path = ''
as $$
declare
  v_period text;
  v_anchor int;
  v_total numeric;
begin
  select period, reset_anchor into v_period, v_anchor
    from public.consumption_units where connection_id = p_connection and unit = p_unit;
  if not found then
    return 0;
  end if;
  if v_period = 'rolling_5h' then
    select coalesce(sum(case when status = 'committed' then coalesce(actual, amount) else amount end), 0)
      into v_total
      from public.quota_reservations
     where connection_id = p_connection and unit = p_unit
       and ((status = 'reserved') or (status = 'committed' and updated_at > p_at - interval '5 hours'));
    return v_total;
  end if;
  select coalesce(used + reserved, 0) into v_total
    from public.quota_usage
   where connection_id = p_connection and unit = p_unit
     and period_start = public.farm_period_start(v_period, v_anchor, p_at);
  return coalesce(v_total, 0);
end;
$$;

-- farm_reserve (0001) plus the sliding window of rolling_5h: the guard compares the window, not the lifetime
-- counter. The unit's quota_usage row is locked first, so concurrent reservers of one unit are serialised.
create or replace function public.farm_reserve(
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

  insert into public.quota_usage (connection_id, unit, period_start, used, reserved, limit_value)
  values (p_connection, p_unit, v_period_start, 0, 0, v_limit)
  on conflict (connection_id, unit, period_start)
  do update set limit_value = excluded.limit_value
  where public.quota_usage.limit_value is distinct from excluded.limit_value;

  if v_period = 'rolling_5h' then
    perform 1 from public.quota_usage
     where connection_id = p_connection and unit = p_unit and period_start = v_period_start
       for update;
    if v_limit is not null
       and public.farm_quota_consumed(p_connection, p_unit, now()) + p_amount > v_limit then
      return null;
    end if;
    update public.quota_usage set reserved = reserved + p_amount
     where connection_id = p_connection and unit = p_unit and period_start = v_period_start;
  else
    update public.quota_usage
       set reserved = reserved + p_amount
     where connection_id = p_connection
       and unit = p_unit
       and period_start = v_period_start
       and (limit_value is null or used + reserved + p_amount <= limit_value);
    if not found then
      return null;
    end if;
  end if;

  insert into public.quota_reservations (connection_id, unit, amount, request_id, period_start, expires_at)
  values (p_connection, p_unit, p_amount, p_request, v_period_start,
          now() + make_interval(secs => greatest(coalesce(p_ttl_seconds, 300), 0)))
  returning id into v_id;
  return v_id;
end;
$$;

-- farm_commit (0001) plus the late commit: a call that outlived its reservation's TTL still did its work, so
-- its actual usage is added to used (reserved was already given back by the expiry) and the row says so.
create or replace function public.farm_commit(p_reservation uuid, p_actual numeric default null)
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
  if not found or v_res.status not in ('reserved', 'expired') then
    return;
  end if;

  v_actual := coalesce(p_actual, v_res.amount);

  if v_res.status = 'expired' then
    if v_actual > 0 then
      update public.quota_usage
         set used = used + v_actual
       where connection_id = v_res.connection_id
         and unit = v_res.unit
         and period_start = v_res.period_start;
      update public.quota_reservations
         set status = 'committed', actual = v_actual, late = true
       where id = v_res.id;
    end if;
    return;
  end if;

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

-- Hold p_amount (USD) against every applicable hard-stop budget (the connection's, its provider's, the global one)
-- for the month starting p_month_start. One advisory lock serialises the check and the insert, so concurrent
-- calls see each other's holds. Returns {"id": <hold>} or {"blocked": <scope>, "ref", "budget", "spend"}.
create function public.farm_budget_reserve(
  p_connection text,
  p_amount numeric,
  p_request uuid,
  p_now timestamptz,
  p_month_start timestamptz,
  p_ttl_seconds int
) returns jsonb
language plpgsql
set search_path = ''
as $$
declare
  v_provider text;
  v_scope text;
  v_ref text;
  v_budget numeric;
  v_hard boolean;
  v_usage numeric;
  v_billing numeric;
  v_held numeric;
  v_spend numeric;
  v_id uuid;
begin
  select provider_id into v_provider from public.connections where id = p_connection;
  if not found then
    return jsonb_build_object('unknown_connection', true);
  end if;

  perform pg_advisory_xact_lock(hashtextextended('farm_budget_reserve', 0));
  delete from public.budget_reservations where expires_at < p_now - interval '1 day';

  for v_scope, v_ref in
    select * from (values ('connection', p_connection), ('provider', v_provider), ('global', null::text)) as s(scope, ref)
  loop
    v_budget := null;
    v_hard := true;
    if v_scope = 'global' then
      select monthly_usd, hard_stop into v_budget, v_hard
        from public.budgets where scope = 'global' and (ref is null or ref = 'global') limit 1;
      if not found then
        v_hard := true;
        select global_monthly_budget_usd into v_budget from public.farm_settings where id = 1;
      end if;
    else
      select monthly_usd, hard_stop into v_budget, v_hard
        from public.budgets where scope = v_scope and ref = v_ref limit 1;
    end if;
    if v_budget is null or not v_hard then
      continue;
    end if;

    select coalesce(sum(u.cost_usd), 0) into v_usage
      from public.usage_events u join public.connections c on c.id = u.connection_id
     where u.at >= p_month_start
       and (v_scope = 'global' or (v_scope = 'connection' and c.id = v_ref) or (v_scope = 'provider' and c.provider_id = v_ref));
    select coalesce(sum(case when b.kind = 'refund' then -b.amount_usd else b.amount_usd end), 0) into v_billing
      from public.billing_events b join public.connections c on c.id = b.connection_id
     where b.at >= p_month_start
       and (v_scope = 'global' or (v_scope = 'connection' and c.id = v_ref) or (v_scope = 'provider' and c.provider_id = v_ref));
    select coalesce(sum(r.amount_usd), 0) into v_held
      from public.budget_reservations r join public.connections c on c.id = r.connection_id
     where r.expires_at > p_now
       and (v_scope = 'global' or (v_scope = 'connection' and c.id = v_ref) or (v_scope = 'provider' and c.provider_id = v_ref));

    v_spend := greatest(0, v_usage + v_billing);
    if v_spend + v_held + p_amount > v_budget then
      return jsonb_build_object(
        'blocked', v_scope, 'ref', v_ref, 'budget', v_budget, 'spend', v_spend, 'held', v_held);
    end if;
  end loop;

  insert into public.budget_reservations (connection_id, amount_usd, request_id, expires_at)
  values (p_connection, p_amount, p_request, p_now + make_interval(secs => greatest(coalesce(p_ttl_seconds, 300), 0)))
  returning id into v_id;
  return jsonb_build_object('id', v_id);
end;
$$;

-- farm_reactivate_due (0005) without the debug string_agg / RAISE NOTICE. A connection that is no longer
-- exhausted also loses the stale next_reset_at of its units (the window has reset).
create or replace function public.farm_reactivate_due(p_now timestamptz default now())
returns int language plpgsql as $$
declare
  v_count int;
begin
  with due as (
    select c.id
    from public.connections c
    left join public.connection_health h on h.connection_id = c.id
    where c.status = 'exhausted'
      and (h.cooldown_until is null or h.cooldown_until <= p_now)
  ),
  reactivated as (
    update public.connections c
    set status = 'active'
    from due
    where c.id = due.id
    returning c.id
  )
  select count(*) into v_count from reactivated;

  update public.consumption_units cu
     set next_reset_at = null
    from public.connections c
   where c.id = cu.connection_id and c.status <> 'exhausted'
     and cu.next_reset_at is not null and cu.next_reset_at <= p_now;

  update public.connection_health h
     set cooldown_until = null
    from public.connections c
   where c.id = h.connection_id and c.status = 'active'
     and h.cooldown_until is not null and h.cooldown_until <= p_now;

  return v_count;
end;
$$;
"""
    + REVOKE_SQL
)

DOWNGRADE_SQL = """
drop function if exists public.farm_budget_reserve(text, numeric, uuid, timestamptz, timestamptz, int);
drop table if exists public.budget_reservations;

create or replace function public.farm_reserve(
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
  select period, reset_anchor, limit_value into v_period, v_anchor, v_limit
    from public.consumption_units where connection_id = p_connection and unit = p_unit;
  if not found then
    raise exception using
      message = 'unknown unit ' || coalesce(p_unit, '<null>') || ' for connection ' || coalesce(p_connection, '<null>'),
      errcode = 'P0002';
  end if;
  v_period_start := public.farm_period_start(v_period, v_anchor, now());
  insert into public.quota_usage (connection_id, unit, period_start, used, reserved, limit_value)
  values (p_connection, p_unit, v_period_start, 0, 0, v_limit)
  on conflict (connection_id, unit, period_start)
  do update set limit_value = excluded.limit_value
  where public.quota_usage.limit_value is distinct from excluded.limit_value;
  update public.quota_usage set reserved = reserved + p_amount
   where connection_id = p_connection and unit = p_unit and period_start = v_period_start
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

create or replace function public.farm_commit(p_reservation uuid, p_actual numeric default null)
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
     set reserved = greatest(reserved - v_res.amount, 0), used = used + v_actual
   where connection_id = v_res.connection_id and unit = v_res.unit and period_start = v_res.period_start;
  update public.quota_reservations set status = 'committed', actual = v_actual where id = v_res.id;
end;
$$;

drop function if exists public.farm_quota_consumed(text, text, timestamptz);

create or replace function public.farm_reactivate_due(p_now timestamptz default now())
returns int language plpgsql as $$
declare
  v_count int;
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

  update public.connection_health
  set cooldown_until = null
  where connection_id in (
    select h.connection_id
    from public.connections c
    left join connection_health h on h.connection_id = c.id
    where c.status = 'active' and h.cooldown_until <= p_now
  );
  return v_count;
end;
$$;

alter table public.quota_reservations drop column if exists late;
alter table public.connection_health drop column if exists recent_outcomes;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
