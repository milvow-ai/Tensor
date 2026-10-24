"""Q0G follow-ups: the dashboard tells the truth about probing accounts and rolling windows (D39, D42).

* ``v_connection_status`` (D39): an account whose circuit is half-open (a probe lease is held) has
  ``effective_state = 'probing'`` instead of ``active``; an open circuit stays ``circuit_open``. Neither counts
  as usable in ``v_pool_overview``.
* ``v_connection_status`` (D42): for ``rolling_5h`` units ``used`` / ``reserved`` are the sliding 5-hour sums
  of committed / held reservations (what ``farm_quota_consumed`` limits on), not the lifetime counter, and a
  ``next_reset_at`` that has passed is not shown.
* ``farm_reactivate_due`` (D42): clears every ``next_reset_at`` that has passed, not only those of accounts
  that are no longer exhausted.

D40 (one budget rule) and D41 (an overrun blocks the account) are Python-side and need no schema change.
Column lists of the view are unchanged, so ``create or replace`` keeps dependants and grants.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-24 00:00:18.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANSWERED = """not r.cached
      and not exists (
        select 1 from public.run_events e
        where e.run_id = r.id and e.kind in ('cache_hit', 'single_flight_join')
      )"""

# {r_answered}, {probing}, {next_reset}, {used}, {reserved} and {window} are filled in below.
CONNECTION_STATUS = """
create or replace view v_connection_status with (security_invoker = true) as
with unit_state as (
  select
    u.connection_id,
    u.unit,
    u.period,
    u.reset_anchor,
    {next_reset},
    u.charged_on,
    u.unit_cost_usd,
    u.estimate_per_call,
    {used} as used,
    {reserved} as reserved,
    coalesce(q.limit_value, u.limit_value) as limit_value
  from consumption_units u
  left join quota_usage q
    on q.connection_id = u.connection_id
   and q.unit = u.unit
   and q.period_start = farm_period_start(u.period, u.reset_anchor, now())
{window}
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
{probing}    when h.cooldown_until is not null and h.cooldown_until > now() then 'cooldown'
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

WINDOW = """left join lateral (
    select
      coalesce(sum(case when r.status = 'committed' then coalesce(r.actual, r.amount) else 0 end), 0) as used,
      coalesce(sum(case when r.status = 'reserved' then r.amount else 0 end), 0) as reserved
    from quota_reservations r
    where u.period = 'rolling_5h' and r.connection_id = u.connection_id and r.unit = u.unit
      and (r.status = 'reserved' or (r.status = 'committed' and r.updated_at > now() - interval '5 hours'))
  ) w on true"""


def _view(*, new: bool) -> str:
    return (
        CONNECTION_STATUS.replace(
            "{probing}", "    when h.circuit = 'half_open' then 'probing'\n" if new else ""
        )
        .replace(
            "{next_reset}",
            "case when u.next_reset_at > now() then u.next_reset_at end as next_reset_at"
            if new
            else "u.next_reset_at",
        )
        .replace(
            "{used}",
            "case when u.period = 'rolling_5h' then w.used else coalesce(q.used, 0) end"
            if new
            else "coalesce(q.used, 0)",
        )
        .replace(
            "{reserved}",
            "case when u.period = 'rolling_5h' then w.reserved else coalesce(q.reserved, 0) end"
            if new
            else "coalesce(q.reserved, 0)",
        )
        .replace("{window}", "  " + WINDOW if new else "")
        .replace("{r_answered}", ANSWERED)
    )


REACTIVATE_DUE = """create or replace function public.farm_reactivate_due(p_now timestamptz default now())
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
   where c.id = cu.connection_id %COND%
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


def upgrade() -> None:
    op.execute(_view(new=True))
    op.execute(REACTIVATE_DUE.replace("%COND%", ""))


def downgrade() -> None:
    op.execute(_view(new=False))
    op.execute(REACTIVATE_DUE.replace("%COND%", "and c.status <> 'exhausted'"))
