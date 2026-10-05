-- =====================================================================================================================
-- Farm Console C2 views (Alembic migration wraps this file verbatim).
--
-- Views supporting the Billing and Runs screens:
--   * v_spend_daily: Daily spend per provider for stacked area charts (30/90 days).
--   * v_cost_per_result: Spend ÷ successful results this month per provider and connection.
--   * v_renewals: Upcoming paid renewals in the next 45 days with usage % of allowance.
--   * v_idle_paid: Paid connections with zero successful calls in the last 14 days.
--   * v_run_detail: Full run information with ordered run_events and final connection / evidence info.
-- =====================================================================================================================

-- ---------------------------------------------------------------------------------------------------------------------
-- v_spend_daily: date × provider spend over all recorded usage and billing events.
--
--   day                          UTC calendar day (date)
--   provider_id                  providers.id
--   provider_name                providers.name
--   usage_usd                    sum of usage_events.cost_usd on that day
--   billing_usd                  sum of billing_events.amount_usd (refunds subtract) on that day
--   spend_usd                    usage_usd + billing_usd
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_spend_daily with (security_invoker = true) as
with daily_usage as (
  select
    date_trunc('day', e.at)::date as day,
    c.provider_id,
    sum(e.cost_usd) as usage_usd
  from usage_events e
  join connections c on c.id = e.connection_id
  group by 1, 2
),
daily_billing as (
  select
    date_trunc('day', b.at)::date as day,
    c.provider_id,
    sum(case when b.kind = 'refund' then -b.amount_usd else b.amount_usd end) as billing_usd
  from billing_events b
  join connections c on c.id = b.connection_id
  group by 1, 2
),
days_and_providers as (
  select day, provider_id from daily_usage
  union
  select day, provider_id from daily_billing
)
select
  dp.day,
  dp.provider_id,
  p.name as provider_name,
  coalesce(u.usage_usd, 0)::numeric as usage_usd,
  coalesce(b.billing_usd, 0)::numeric as billing_usd,
  (coalesce(u.usage_usd, 0) + coalesce(b.billing_usd, 0))::numeric as spend_usd
from days_and_providers dp
join providers p on p.id = dp.provider_id
left join daily_usage u on u.day = dp.day and u.provider_id = dp.provider_id
left join daily_billing b on b.day = dp.day and b.provider_id = dp.provider_id
order by dp.day desc, dp.provider_id;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_cost_per_result: spend ÷ successful results this month per provider and connection.
--
--   connection_id                connections.id
--   connection_label             connections.label
--   provider_id                  providers.id
--   provider_name                providers.name
--   month_start                  start of current UTC month
--   spend_usd                    sum of usage and billing events this month
--   successful_results           count of successful runs on this connection this month
--   cost_per_result              spend_usd / successful_results (null if 0 results)
-- ---------------------------------------------------------------------------------------------------------------------
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
order by p.name, c.priority, c.id;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_renewals: upcoming renewals in the next 45 days with allowance usage percentage.
--
--   connection_id                connections.id
--   connection_label             connections.label
--   provider_id                  providers.id
--   provider_name                providers.name
--   plan_name                    connections.plan->>'name'
--   price_usd                    connections.plan->>'price_usd' numeric
--   billing_day                  connections.plan->>'billing_day' int
--   renews_on                    timestamptz of next renewal date
--   days_until_renewal           number of days remaining until renewal
--   used                         units used in current period
--   limit_value                  allowance limit (null if unlimited)
--   usage_pct                    used ÷ limit_value * 100 (null if unlimited or limit is 0)
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_renewals with (security_invoker = true) as
with active_plans as (
  select
    c.id as connection_id,
    coalesce(c.label, c.id) as connection_label,
    p.id as provider_id,
    p.name as provider_name,
    c.plan ->> 'name' as plan_name,
    case when jsonb_typeof(c.plan -> 'price_usd') = 'number' then (c.plan ->> 'price_usd')::numeric else 0 end as price_usd,
    case when jsonb_typeof(c.plan -> 'billing_day') = 'number' then (c.plan ->> 'billing_day')::int else null end as billing_day,
    case
      when c.plan ->> 'renews_on' is not null then (c.plan ->> 'renews_on')::timestamptz
      when jsonb_typeof(c.plan -> 'billing_day') = 'number' then
        farm_period_start('month', (c.plan ->> 'billing_day')::int, now()) + interval '1 month'
      else null
    end as renews_on
  from connections c
  join providers p on p.id = c.provider_id
  where c.status <> 'disabled'
    and jsonb_typeof(c.plan -> 'price_usd') = 'number'
    and (c.plan ->> 'price_usd')::numeric > 0
),
quota_summary as (
  select
    u.connection_id,
    coalesce(q.used, 0) as used,
    coalesce(q.limit_value, u.limit_value) as limit_value
  from consumption_units u
  left join quota_usage q
    on q.connection_id = u.connection_id
   and q.unit = u.unit
   and q.period_start = farm_period_start(u.period, u.reset_anchor, now())
  where u.period = 'month'
)
select
  ap.connection_id,
  ap.connection_label,
  ap.provider_id,
  ap.provider_name,
  ap.plan_name,
  ap.price_usd,
  ap.billing_day,
  ap.renews_on,
  extract(day from (ap.renews_on - now()))::int as days_until_renewal,
  qs.used,
  qs.limit_value,
  case
    when qs.limit_value is not null and qs.limit_value > 0 then
      round((qs.used / qs.limit_value) * 100, 1)
    else null
  end as usage_pct
from active_plans ap
left join quota_summary qs on qs.connection_id = ap.connection_id
where ap.renews_on is not null
  and ap.renews_on >= now()
  and ap.renews_on <= now() + interval '45 days'
order by ap.renews_on asc;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_idle_paid: paid connections with 0 successful calls in the last 14 days.
--
--   connection_id                connections.id
--   connection_label             connections.label
--   provider_id                  providers.id
--   provider_name                providers.name
--   plan_name                    connections.plan->>'name'
--   plan_price_usd               price per month
--   status                       connections.status
--   last_success_at              most recent successful run timestamp
--   days_idle                    days since last success (or 14+ if none ever)
-- ---------------------------------------------------------------------------------------------------------------------
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
)
order by pc.plan_price_usd desc, pc.connection_id;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_run_detail: run + ordered events + final connection + evidence ids.
--
--   id                           runs.id
--   capability                   runs.capability
--   request_id                   runs.request_id
--   caller                       runs.caller
--   strategy                     runs.strategy
--   status                       runs.status
--   cost_usd                     runs.cost_usd
--   cached                       runs.cached
--   connection_id                runs.connection_id
--   connection_label             label of final connection
--   provider_id                  providers.id of final connection
--   provider_name                providers.name of final connection
--   error_kind                   runs.error_kind
--   error                        runs.error
--   started_at                   runs.started_at
--   finished_at                  runs.finished_at
--   duration_ms                  execution duration in milliseconds
--   params                       capability_requests.params
--   result                       capability_requests.result
--   events                       jsonb array of ordered run_events
--   evidence_ids                 uuid[] from facts or run
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_run_detail with (security_invoker = true) as
select
  r.id,
  r.capability,
  r.request_id,
  r.caller,
  r.strategy,
  r.status,
  r.cost_usd,
  r.cached,
  r.connection_id,
  coalesce(c.label, c.id) as connection_label,
  c.provider_id,
  p.name as provider_name,
  r.error_kind,
  r.error,
  r.started_at,
  r.finished_at,
  case
    when r.finished_at is null then null
    else round(extract(epoch from (r.finished_at - r.started_at)) * 1000)::int
  end as duration_ms,
  cr.params,
  cr.result,
  coalesce(
    (
      select jsonb_agg(
        jsonb_build_object(
          'id', e.id,
          'seq', e.seq,
          'kind', e.kind,
          'connection_id', e.connection_id,
          'data', e.data,
          'at', e.at
        )
        order by e.seq asc, e.at asc
      )
      from run_events e
      where e.run_id = r.id
    ),
    '[]'::jsonb
  ) as events,
  coalesce(
    (
      select array_agg(distinct ev_id)
      from facts f,
      unnest(f.evidence_ids) as ev_id
      where f.source_connection_id = r.connection_id
        and f.observed_at >= r.started_at
        and f.observed_at <= coalesce(r.finished_at, now())
    ),
    array[]::uuid[]
  ) as evidence_ids
from runs r
left join connections c on c.id = r.connection_id
left join providers p on p.id = c.provider_id
left join capability_requests cr on cr.id = r.request_id;
