-- =====================================================================================================================
-- Farm Console views (Alembic migration 0003 wraps this file verbatim).
--
-- Plain PostgreSQL 16 SQL. Every view is `security_invoker = true`, so the caller's RLS applies: the owner may
-- select every table (CONTEXT section 3) and nobody else sees a row. All aggregation lives here; the Console only
-- formats. Order matters: v_pool_overview reads v_connection_status, v_capability_capacity reads v_pool_overview.
--
-- Conventions used by every view:
--   * Month boundaries are UTC calendar months (`date_trunc('month', now())`, session time zone UTC).
--   * "Usable" account = effective_state 'active': status active, circuit not open, no cooldown running.
--   * "Calls remaining" normalises units that differ per provider (credits, messages, searches) into calls:
--     floor(remaining / estimate_per_call) per unit; an account's figure is its tightest unit.
-- =====================================================================================================================


-- ---------------------------------------------------------------------------------------------------------------------
-- v_connection_status: one row per (connection, consumption unit); connections without a unit appear once with null
-- unit columns. Current-period quota is the quota_usage row whose period_start equals farm_period_start(...) now.
--
--   connection_id, provider_id   connections.id / connections.provider_id
--   provider_name, provider_kind providers.name / providers.kind ('tool' | 'ai')
--   label                        connections.label, falls back to the id
--   auth_ref                     env:NAME | token-store:ID | cli:PROFILE (a reference, never a secret)
--   scope, priority              connections.scope / connections.priority (lower priority is tried first)
--   strategy                     the connection override, else the pool default strategy
--   concurrency, rate_per_min    connections.concurrency / connections.rate_per_min
--   status                       connections.status (active, paused, needs_login, exhausted, disabled)
--   effective_state              status refined by health: circuit_open (circuit open), cooldown (cooldown_until in
--                                the future), otherwise status
--   plan, plan_price_usd         connections.plan jsonb (name, price_usd, billing_day, renews_on) and the price as number
--   meta                         connections.meta jsonb (AI accounts: cli, config_dir, models)
--   circuit .. latency_ms_p50    connection_health columns (defaults when no health row exists yet)
--   sessions_count               number of ai_sessions pinned to this connection
--   calls_today                  runs whose final connection is this one, started since 00:00 UTC
--   unit, period, reset_anchor   consumption_units columns
--   next_reset_at, charged_on    consumption_units columns
--   unit_cost_usd                consumption_units.unit_cost_usd
--   estimate_per_call            units one call is expected to consume (default 1)
--   used, reserved               current-period quota_usage (0 when no row yet)
--   limit_value                  current-period limit (quota_usage snapshot, else consumption_units); null = unlimited
--   remaining                    greatest(0, limit_value - used - reserved); null when unlimited
--   calls_remaining              floor(remaining / estimate_per_call); null when unlimited
-- ---------------------------------------------------------------------------------------------------------------------
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
    where r.connection_id = c.id and r.started_at >= date_trunc('day', now()))::int as calls_today,
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
left join unit_state us on us.connection_id = c.id;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_pool_overview: one row per provider pool (tool or AI), aggregated from v_connection_status.
--
--   provider_id, provider_name   providers.id / providers.name
--   kind, executor               providers.kind ('tool' | 'ai') / providers.executor
--   default_strategy, enabled    providers.default_strategy / providers.enabled
--   accounts_total               connections in the pool
--   accounts_active              status = 'active' (includes accounts currently in cooldown or circuit_open)
--   accounts_paused              status = 'paused'
--   accounts_needs_login         status = 'needs_login'
--   accounts_exhausted           status = 'exhausted'
--   accounts_open_circuit        effective_state = 'circuit_open'
--   accounts_cooldown            effective_state = 'cooldown'
--   accounts_usable              effective_state = 'active' (what the router can use right now)
--   account_dots                 jsonb array [{id, label, state}] ordered by priority; state is effective_state
--   health                       'disabled' | 'down' (no usable account) | 'degraded' (an account needs login, is
--                                exhausted, in cooldown or circuit_open) | 'healthy'
--   remaining_calls              sum over usable accounts of their tightest unit's calls_remaining (unlimited accounts
--                                add nothing; see `unlimited`)
--   unlimited                    true when a usable account has no limited unit
--   monthly_plan_usd             sum of plan prices of the pool's non-disabled accounts
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_pool_overview with (security_invoker = true) as
with account as (
  select
    s.connection_id,
    s.provider_id,
    s.label,
    s.priority,
    s.status,
    s.effective_state,
    coalesce(s.plan_price_usd, 0) as plan_price_usd,
    min(s.calls_remaining) as calls_remaining,
    count(s.calls_remaining) = 0 as no_limit
  from v_connection_status s
  group by s.connection_id, s.provider_id, s.label, s.priority, s.status, s.effective_state, s.plan_price_usd
)
select
  p.id as provider_id,
  p.name as provider_name,
  p.kind,
  p.executor,
  p.default_strategy,
  p.enabled,
  count(a.connection_id)::int as accounts_total,
  (count(*) filter (where a.status = 'active'))::int as accounts_active,
  (count(*) filter (where a.status = 'paused'))::int as accounts_paused,
  (count(*) filter (where a.status = 'needs_login'))::int as accounts_needs_login,
  (count(*) filter (where a.status = 'exhausted'))::int as accounts_exhausted,
  (count(*) filter (where a.effective_state = 'circuit_open'))::int as accounts_open_circuit,
  (count(*) filter (where a.effective_state = 'cooldown'))::int as accounts_cooldown,
  (count(*) filter (where a.effective_state = 'active'))::int as accounts_usable,
  coalesce(
    jsonb_agg(
      jsonb_build_object('id', a.connection_id, 'label', a.label, 'state', a.effective_state)
      order by a.priority, a.connection_id
    ) filter (where a.connection_id is not null),
    '[]'::jsonb
  ) as account_dots,
  case
    when not p.enabled then 'disabled'
    when count(*) filter (where a.effective_state = 'active') = 0 then 'down'
    when count(*) filter (where a.effective_state in ('needs_login', 'exhausted', 'circuit_open', 'cooldown')) > 0
      then 'degraded'
    else 'healthy'
  end as health,
  coalesce(sum(a.calls_remaining) filter (where a.effective_state = 'active'), 0)::numeric as remaining_calls,
  coalesce(bool_or(a.no_limit) filter (where a.effective_state = 'active'), false) as unlimited,
  coalesce(sum(a.plan_price_usd) filter (where a.status <> 'disabled'), 0)::numeric as monthly_plan_usd
from providers p
left join account a on a.provider_id = p.id
group by p.id, p.name, p.kind, p.executor, p.default_strategy, p.enabled;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_spend_month: month-to-date spend per provider plus one row with provider_id = 'total'.
--
--   provider_id, provider_name   providers.id / providers.name; 'total' / 'Total' for the aggregate row
--   kind                         'tool' | 'ai' | 'all' (total row)
--   usage_usd                    sum of usage_events.cost_usd since the start of the month
--   billing_usd                  sum of billing_events.amount_usd since the start of the month (refunds subtract)
--   spend_usd                    usage_usd + billing_usd
--   budget_usd                   latest budget for the scope (provider budget; global budget or
--                                farm_settings.global_monthly_budget_usd for the total row); null = none
--   forecast_usd                 spend_usd / elapsed_days * days_in_month, rounded to cents
--   elapsed_days                 fractional days since the month started, at least 1 (avoids day-one blow-ups)
--   days_in_month                number of days in the current month
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_spend_month with (security_invoker = true) as
with clock as (
  select
    date_trunc('month', now()) as month_start,
    greatest(extract(epoch from (now() - date_trunc('month', now()))) / 86400.0, 1.0)::numeric as elapsed_days,
    extract(day from (date_trunc('month', now()) + interval '1 month' - interval '1 day'))::numeric as days_in_month
),
usage as (
  select c.provider_id, sum(e.cost_usd) as usd
  from usage_events e
  join connections c on c.id = e.connection_id
  where e.at >= (select month_start from clock)
  group by c.provider_id
),
billing as (
  select c.provider_id, sum(case when b.kind = 'refund' then -b.amount_usd else b.amount_usd end) as usd
  from billing_events b
  join connections c on c.id = b.connection_id
  where b.at >= (select month_start from clock)
  group by c.provider_id
),
per_provider as (
  select
    p.id as provider_id,
    p.name as provider_name,
    p.kind::text as kind,
    coalesce(u.usd, 0)::numeric as usage_usd,
    coalesce(b.usd, 0)::numeric as billing_usd,
    (
      select bu.monthly_usd
      from budgets bu
      where bu.scope = 'provider' and bu.ref = p.id
      order by bu.created_at desc
      limit 1
    )::numeric as budget_usd
  from providers p
  left join usage u on u.provider_id = p.id
  left join billing b on b.provider_id = p.id
),
total_row as (
  select
    'total'::text as provider_id,
    'Total'::text as provider_name,
    'all'::text as kind,
    coalesce(sum(pp.usage_usd), 0)::numeric as usage_usd,
    coalesce(sum(pp.billing_usd), 0)::numeric as billing_usd,
    coalesce(
      (select bu.monthly_usd from budgets bu where bu.scope = 'global' order by bu.created_at desc limit 1),
      (select fs.global_monthly_budget_usd from farm_settings fs limit 1)
    )::numeric as budget_usd
  from per_provider pp
)
select
  x.provider_id,
  x.provider_name,
  x.kind,
  x.usage_usd,
  x.billing_usd,
  (x.usage_usd + x.billing_usd) as spend_usd,
  x.budget_usd,
  round((x.usage_usd + x.billing_usd) / clock.elapsed_days * clock.days_in_month, 2) as forecast_usd,
  clock.elapsed_days,
  clock.days_in_month
from (
  select * from per_provider
  union all
  select * from total_row
) x
cross join clock;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_capability_capacity: one row per (capability, pool) in route order (capability_routes.position ascending).
--
--   capability, kind             capabilities.name / capabilities.kind
--   description                  capabilities.description
--   default_strategy             capabilities.default_strategy
--   route_position, route_enabled capability_routes.position / capability_routes.enabled
--   provider_id, provider_name   the pool at this step of the route
--   health                       pool health from v_pool_overview
--   accounts_usable, accounts_total pool account counts from v_pool_overview
--   account_dots                 pool account dots from v_pool_overview
--   remaining_calls, unlimited   pool capacity from v_pool_overview
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_capability_capacity with (security_invoker = true) as
select
  cap.name as capability,
  cap.kind,
  cap.description,
  cap.default_strategy,
  cr.position as route_position,
  cr.enabled as route_enabled,
  po.provider_id,
  po.provider_name,
  po.health,
  po.accounts_usable,
  po.accounts_total,
  po.account_dots,
  po.remaining_calls,
  po.unlimited
from capabilities cap
join capability_routes cr on cr.capability = cap.name
join v_pool_overview po on po.provider_id = cr.provider_id;


-- ---------------------------------------------------------------------------------------------------------------------
-- v_recent_runs: runs joined with the final connection and the number of execution attempts. Consumers order by
-- started_at desc and limit.
--
--   id, capability, request_id   runs.id / runs.capability / runs.request_id
--   caller, strategy, status     runs.caller (claude, hermes, console, cli, test) / runs.strategy / runs.status
--   cost_usd, cached             runs.cost_usd / runs.cached
--   connection_id                final connection (null when cached or blocked before execution)
--   connection_label             label of the final connection
--   provider_id, provider_name   pool of the final connection
--   error_kind, error            runs.error_kind / runs.error (already redacted by the Farm)
--   started_at, finished_at      runs.started_at / runs.finished_at (null while running)
--   duration_ms                  finished_at - started_at in milliseconds (null while running)
--   attempts_count               number of 'execute' run_events (initial attempt plus fallbacks)
-- ---------------------------------------------------------------------------------------------------------------------
create or replace view v_recent_runs with (security_invoker = true) as
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
  coalesce(ev.attempts, 0)::int as attempts_count
from runs r
left join connections c on c.id = r.connection_id
left join providers p on p.id = c.provider_id
left join lateral (
  select count(*) as attempts
  from run_events e
  where e.run_id = r.id and e.kind = 'execute'
) ev on true;
