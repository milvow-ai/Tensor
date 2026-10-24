"""Q0 control fixes (Q0E): command claim lease, once-per-ref alerts, one renewal-date rule.

* ``farm_commands.claimed_by`` / ``claimed_at``: a command is claimed by one ``update ... where status = 'queued'``
  and a sweep fails commands whose claim outlived the lease (Q0-02, Q0-17).
* ``idx_alerts_once_per_ref``: a budget-threshold or renewal alert exists once per ref (month / billing cycle),
  acknowledged or not (Q0-06). Existing duplicates keep the oldest row; the others get a ``#dup-<id>`` ref suffix.
* ``farm_next_renewal(billing_day, at)``: the next renewal date, billing day clamped to the month's last day,
  never rolled into the next month (Q0-16). ``v_renewals`` uses it, like the reminder job in Python.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-24 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENEWALS_VIEW = """
create or replace view public.v_renewals with (security_invoker = true) as
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
        @@RENEWAL@@
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
"""

UPGRADE_SQL = """
alter table public.farm_commands
  add column if not exists claimed_by text,
  add column if not exists claimed_at timestamptz;

create index if not exists idx_farm_commands_running_claimed
  on public.farm_commands (claimed_at) where status = 'running';

update public.alerts a set ref = a.ref || '#dup-' || a.id
from (
  select id, row_number() over (partition by tenant_id, ref order by created_at, id) as rn
  from public.alerts
  where kind in ('budget_threshold', 'renewal_reminder') and ref is not null
) d
where a.id = d.id and d.rn > 1;

create unique index if not exists idx_alerts_once_per_ref
  on public.alerts (tenant_id, ref) where kind in ('budget_threshold', 'renewal_reminder');

create or replace function public.farm_next_renewal(p_billing_day int, p_at timestamptz)
returns date
language sql
stable
as $fn$
  select case when c.this_month >= c.today then c.this_month else c.next_month end
  from (
    select
      d.today,
      d.m + (least(greatest(p_billing_day, 1),
                   extract(day from d.m + interval '1 month' - interval '1 day')::int) - 1) as this_month,
      (d.m + interval '1 month')::date + (least(greatest(p_billing_day, 1),
                   extract(day from d.m + interval '2 months' - interval '1 day')::int) - 1) as next_month
    from (
      select (p_at at time zone 'UTC')::date as today,
             date_trunc('month', p_at at time zone 'UTC')::date as m
    ) d
  ) c
$fn$;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    grant execute on function public.farm_next_renewal(int, timestamptz) to authenticated;
  end if;
end $$;

""" + RENEWALS_VIEW.replace("@@RENEWAL@@", "(farm_next_renewal((c.plan ->> 'billing_day')::int, now())::timestamp at time zone 'UTC')")

DOWNGRADE_SQL = """
""" + RENEWALS_VIEW.replace("@@RENEWAL@@", "farm_period_start('month', (c.plan ->> 'billing_day')::int, now()) + interval '1 month'") + """

drop function if exists public.farm_next_renewal(int, timestamptz);
drop index if exists public.idx_alerts_once_per_ref;
drop index if exists public.idx_farm_commands_running_claimed;
alter table public.farm_commands drop column if exists claimed_at, drop column if exists claimed_by;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
