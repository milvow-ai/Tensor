"""Durable project tasks, operator leases, step receipts and live worker activity."""

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    create table public.farm_tasks (
      tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
      id uuid not null default gen_random_uuid(),
      owner_id uuid references public.users(id) on delete cascade,
      project text not null check (length(project) between 1 and 63),
      workspace text not null check (length(workspace) between 1 and 1024),
      title text not null check (length(title) between 1 and 200),
      goal text not null check (octet_length(goal) between 1 and 32768),
      checkpoint text not null default '' check (octet_length(checkpoint) <= 32768),
      state text not null default 'active' check (state in ('active','paused','done')),
      operator_key text not null,
      operator_client text not null,
      approach text not null check (approach in ('finish','delegate')),
      epoch integer not null default 1 check (epoch > 0),
      lease_until timestamptz not null,
      created_at timestamptz not null default now(),
      updated_at timestamptz not null default now(),
      primary key (tenant_id,id)
    );
    create index farm_tasks_workspace_idx on public.farm_tasks (tenant_id,owner_id,workspace,updated_at desc);

    create table public.farm_task_steps (
      tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
      task_id uuid not null,
      id uuid not null default gen_random_uuid(),
      step_key text not null check (length(step_key) between 1 and 100),
      fingerprint text not null,
      kind text not null check (kind in ('local','cli','mcp','api')),
      label text not null check (length(label) between 1 and 200),
      target text,
      write_root text,
      operator_epoch int not null,
      assigned_by text not null,
      state text not null default 'running' check (state in ('running','paused','succeeded','failed','outcome_unknown')),
      deadline timestamptz,
      receipt jsonb check (octet_length(receipt::text) <= 1048576),
      resolution text check (resolution in ('completed','superseded')),
      resolution_note text check (octet_length(resolution_note) <= 32768),
      resolved_by text,
      created_at timestamptz not null default now(),
      finished_at timestamptz,
      primary key (tenant_id,id),
      unique (tenant_id,task_id,step_key),
      foreign key (tenant_id,task_id) references public.farm_tasks(tenant_id,id) on delete cascade
    );
    create index farm_task_steps_writes_idx on public.farm_task_steps (tenant_id,write_root)
      where write_root is not null;

    create table public.farm_task_events (
      tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
      task_id uuid not null,
      id bigint generated always as identity,
      kind text not null check (kind in ('opened','takeover','paused','completed')),
      actor text not null,
      previous_actor text,
      epoch int not null,
      approach text not null,
      at timestamptz not null default now(),
      primary key (tenant_id,id),
      foreign key (tenant_id,task_id) references public.farm_tasks(tenant_id,id) on delete cascade
    );
    create index farm_task_events_task_idx on public.farm_task_events (tenant_id,task_id,id desc);
    """)
    expressions = {
        "farm_tasks": "public.farm_private_visible(owner_id)",
        "farm_task_steps": "exists(select 1 from public.farm_tasks t where t.tenant_id=farm_task_steps.tenant_id and t.id=farm_task_steps.task_id)",
        "farm_task_events": "exists(select 1 from public.farm_tasks t where t.tenant_id=farm_task_events.tenant_id and t.id=farm_task_events.task_id)",
    }
    for table, expression in expressions.items():
        op.execute(f"""
        alter table public.{table} enable row level security;
        create policy tenant_isolation on public.{table} to farm_app
          using (tenant_id=(select public.farm_tenant_id())) with check (tenant_id=(select public.farm_tenant_id()));
        create policy person_private_work on public.{table} as restrictive to farm_app
          using ({expression}) with check ({expression});
        create policy farm_maintenance on public.{table} to farm_system using(true) with check(true);
        grant select,insert,update,delete on public.{table} to farm_app;
        grant all on public.{table} to farm_system;
        do $b$ begin
          if to_regprocedure('public.farm_member_tenant_ids()') is not null then
            revoke all on public.{table} from anon,authenticated;
            create policy member_select_tasks on public.{table} for select to authenticated
              using (tenant_id in (select public.farm_member_tenant_ids()) and ({expression}));
          end if;
        end $b$;
        """)
    op.execute("""
    grant usage on sequence public.farm_task_events_id_seq to farm_app,farm_system;
    do $b$ begin
      if to_regprocedure('public.farm_member_tenant_ids()') is not null then
        grant select (tenant_id,id,owner_id,project,title,state,operator_client,approach,epoch,lease_until,updated_at)
          on public.farm_tasks to authenticated;
      end if;
    end $b$;

    alter table public.runs add column active_connection_id text,
      add column active_model text, add column active_until timestamptz;
    create index runs_live_workers_idx on public.runs (tenant_id,active_connection_id,active_until)
      where active_connection_id is not null and status='running';

    create view public.v_worker_activity with (security_invoker=true) as
      select c.tenant_id,c.id as account,c.provider_id as provider,p.kind,c.label,c.status,
        case when p.kind='ai' then least(c.concurrency,
          case when jsonb_typeof(c.meta->'max_parallel')='number'
            and (c.meta->>'max_parallel') ~ '^[1-9][0-9]*$' then
            least(c.concurrency,(c.meta->>'max_parallel')::numeric)::int
          else c.concurrency end) else c.concurrency end as slots,
        (select count(*) from public.runs r where r.tenant_id=c.tenant_id
          and r.active_connection_id=c.id and r.active_until>now() and r.status='running') as busy,
        (select count(*) from public.ai_jobs j where j.tenant_id=c.tenant_id and j.account=c.id
          and j.state='queued') as queued,
        (select coalesce(jsonb_agg(m),'[]'::jsonb) from (
          select r.active_model as model,count(*) as jobs from public.runs r
          where r.tenant_id=c.tenant_id and r.active_connection_id=c.id and r.active_until>now()
            and r.status='running' group by r.active_model order by r.active_model
        ) m) as running_models,
        h.cooldown_until,s.status as allowance_status,s.observed_at as allowance_observed_at,
        s.snapshot->'rate_limits' as allowance_windows,
        coalesce(u.poll_interval_seconds,600) as allowance_poll_seconds
      from public.connections c join public.providers p on p.tenant_id=c.tenant_id and p.id=c.provider_id
      left join public.connection_health h on h.tenant_id=c.tenant_id and h.connection_id=c.id
      left join public.ai_usage_snapshots s on s.tenant_id=c.tenant_id and s.connection_id=c.id
      left join public.ai_usage_settings u on u.tenant_id=c.tenant_id and u.id=1
      where p.enabled;
    grant select on public.v_worker_activity to farm_app,farm_system;
    do $b$ begin
      if to_regprocedure('public.farm_member_tenant_ids()') is not null then
        create policy member_select_ai_usage_snapshots on public.ai_usage_snapshots for select
          to authenticated using (tenant_id in (select public.farm_member_tenant_ids()));
        create policy member_select_ai_usage_settings on public.ai_usage_settings for select
          to authenticated using (tenant_id in (select public.farm_member_tenant_ids()));
        grant select (tenant_id,connection_id,status,observed_at,snapshot) on public.ai_usage_snapshots
          to authenticated;
        grant select (tenant_id,id,poll_interval_seconds) on public.ai_usage_settings to authenticated;
        grant select on public.v_worker_activity to authenticated;
      end if;
    end $b$;
    """)


def downgrade() -> None:
    op.execute("""
    drop view public.v_worker_activity;
    drop index public.runs_live_workers_idx;
    alter table public.runs drop column active_connection_id,drop column active_model,drop column active_until;
    drop table public.farm_task_events,public.farm_task_steps,public.farm_tasks;
    do $b$ begin
      if to_regprocedure('public.farm_member_tenant_ids()') is not null then
        drop policy member_select_ai_usage_snapshots on public.ai_usage_snapshots;
        drop policy member_select_ai_usage_settings on public.ai_usage_settings;
        revoke select (tenant_id,connection_id,status,observed_at,snapshot) on public.ai_usage_snapshots
          from authenticated;
        revoke select (tenant_id,id,poll_interval_seconds) on public.ai_usage_settings from authenticated;
      end if;
    end $b$;
    """)
