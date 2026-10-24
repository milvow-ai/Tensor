"""Console numbers count only calls a connection really answered (Q0-09).

``runs.connection_id`` is also set on a run that was answered from the cache or by joining a running call
(it names the account that originally answered). ``v_connection_status.calls_today``,
``v_cost_per_result.successful_results`` and ``v_idle_paid`` counted those runs for that account: calls
inflated, cost per result understated, an idle paid account looking busy. The three views are recreated
here to count only runs that executed on their connection (not ``cached`` and no ``cache_hit`` /
``single_flight_join`` event). Column lists are unchanged, so ``create or replace`` keeps dependants
(``v_pool_overview`` and the grants).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANSWERED = """not r.cached
      and not exists (
        select 1 from public.run_events e
        where e.run_id = r.id and e.kind in ('cache_hit', 'single_flight_join')
      )"""
EVERYTHING = "true"

CONNECTION_STATUS = """
create or replace view v_connection_status with (security_invoker = true) as
with unit_state as (
  select
    u.connection_id,
    u.unit,
    u.period,
    u.reset_anchor,
    u.next_reset_at,
    u.charged_on,
    u.unit_cost_usd,
    u.estimate_per_call,
    coalesce(q.used, 0) as used,
    coalesce(q.reserved, 0) as reserved,
    coalesce(q.limit_value, u.limit_value) as limit_value
  from consumption_units u
  left join quota_usage q
    on q.connection_id = u.connection_id
   and q.unit = u.unit
   and q.period_start = farm_period_start(u.period, u.reset_anchor, now())
)
select
  c.id as connection_id,
  c.provider_id,
  p.name as provider_name,
  p.kind as provider_kind,
  coalesce(c.label, c.id) as label,
  c.auth_ref,
  c.scope,
  c.priority,
  coalesce(c.strategy, p.default_strategy) as strategy,
  c.concurrency,
  c.rate_per_min,
  c.status,
  case
    when c.status <> 'active' then c.status
    when h.circuit = 'open' then 'circuit_open'
    when h.cooldown_until is not null and h.cooldown_until > now() then 'cooldown'
    else 'active'
  end as effective_state,
  c.plan,
  case when jsonb_typeof(c.plan -> 'price_usd') = 'number' then (c.plan ->> 'price_usd')::numeric end as plan_price_usd,
  c.meta,
  coalesce(h.circuit, 'closed') as circuit,
  coalesce(h.consecutive_failures, 0) as consecutive_failures,
  h.last_error_kind,
  h.last_error,
  h.last_error_at,
  h.cooldown_until,
  coalesce(h.success_count, 0) as success_count,
  coalesce(h.failure_count, 0) as failure_count,
  h.last_success_at,
  h.latency_ms_p50,
  (select count(*) from ai_sessions s where s.connection_id = c.id)::int as sessions_count,
  (select count(*) from runs r
    where r.connection_id = c.id and r.started_at >= date_trunc('day', now())
      and {r_answered})::int as calls_today,
  us.unit,
  us.period,
  us.reset_anchor,
  us.next_reset_at,
  us.charged_on,
  us.unit_cost_usd,
  us.estimate_per_call,
  us.used,
  us.reserved,
  us.limit_value,
  case
    when us.unit is null or us.limit_value is null then null
    else greatest(0, us.limit_value - us.used - us.reserved)
  end as remaining,
  case
    when us.unit is null or us.limit_value is null then null
    when coalesce(us.estimate_per_call, 1) <= 0 then greatest(0, us.limit_value - us.used - us.reserved)
    else floor(greatest(0, us.limit_value - us.used - us.reserved) / us.estimate_per_call)
  end as calls_remaining
from connections c
join providers p on p.id = c.provider_id
left join connection_health h on h.connection_id = c.id
left join unit_state us on us.connection_id = c.id
"""

COST_PER_RESULT = """
create or replace view v_cost_per_result with (security_invoker = true) as
with month_clock as (
  select date_trunc('month', now()) as start_at
),
conn_usage as (
  select e.connection_id, sum(e.cost_usd) as usd
  from usage_events e
  where e.at >= (select start_at from month_clock)
  group by e.connection_id
),
conn_billing as (
  select b.connection_id, sum(case when b.kind = 'refund' then -b.amount_usd else b.amount_usd end) as usd
  from billing_events b
  where b.at >= (select start_at from month_clock)
  group by b.connection_id
),
conn_success_runs as (
  select r.connection_id, count(*)::bigint as successes
  from runs r
  where r.status = 'succeeded' and r.started_at >= (select start_at from month_clock)
    and {r_answered}
  group by r.connection_id
)
select
  c.id as connection_id,
  coalesce(c.label, c.id) as connection_label,
  p.id as provider_id,
  p.name as provider_name,
  (select start_at from month_clock) as month_start,
  (coalesce(u.usd, 0) + coalesce(b.usd, 0))::numeric as spend_usd,
  coalesce(sr.successes, 0)::bigint as successful_results,
  case
    when coalesce(sr.successes, 0) > 0 then
      round((coalesce(u.usd, 0) + coalesce(b.usd, 0)) / sr.successes, 4)
    else null
  end as cost_per_result
from connections c
join providers p on p.id = c.provider_id
left join conn_usage u on u.connection_id = c.id
left join conn_billing b on b.connection_id = c.id
left join conn_success_runs sr on sr.connection_id = c.id
order by p.name, c.priority, c.id
"""

IDLE_PAID = """
create or replace view v_idle_paid with (security_invoker = true) as
with paid_connections as (
  select
    c.id as connection_id,
    coalesce(c.label, c.id) as connection_label,
    p.id as provider_id,
    p.name as provider_name,
    c.plan ->> 'name' as plan_name,
    (c.plan ->> 'price_usd')::numeric as plan_price_usd,
    c.status,
    h.last_success_at
  from connections c
  join providers p on p.id = c.provider_id
  left join connection_health h on h.connection_id = c.id
  where c.status <> 'disabled'
    and jsonb_typeof(c.plan -> 'price_usd') = 'number'
    and (c.plan ->> 'price_usd')::numeric > 0
)
select
  pc.connection_id,
  pc.connection_label,
  pc.provider_id,
  pc.provider_name,
  pc.plan_name,
  pc.plan_price_usd,
  pc.status,
  pc.last_success_at,
  case
    when pc.last_success_at is not null then
      extract(epoch from (now() - pc.last_success_at)) / 86400.0
    else 999.0
  end::numeric(6, 1) as days_idle
from paid_connections pc
where not exists (
  select 1
  from runs r
  where r.connection_id = pc.connection_id
    and r.status = 'succeeded'
    and r.started_at >= now() - interval '14 days'
    and {r_answered}
)
order by pc.plan_price_usd desc, pc.connection_id
"""


def _recreate(r_answered: str) -> None:
    for view in (CONNECTION_STATUS, COST_PER_RESULT, IDLE_PAID):
        op.execute(view.replace("{r_answered}", r_answered))


def upgrade() -> None:
    _recreate(ANSWERED)


def downgrade() -> None:
    _recreate(EVERYTHING)
