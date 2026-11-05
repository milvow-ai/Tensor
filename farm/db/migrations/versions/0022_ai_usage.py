"""Tenant scoped provider allowance snapshots for AI usage monitoring.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = r"""
alter table public.connections add constraint connections_tenant_id_id_key unique (tenant_id, id);
create table public.ai_usage_settings (
 tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
 id int not null default 1 check(id=1), poll_interval_seconds int not null default 600 check(poll_interval_seconds between 60 and 86400),
 primary key(tenant_id,id)
);
create table public.ai_usage_snapshots (
 tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
 connection_id text not null,
 provider_id text not null, status text not null check(status in ('ok','error','unsupported','unconfigured')),
 error_code text, error_streak int not null default 0 check(error_streak >= 0), observed_at timestamptz, checked_at timestamptz not null default now(), snapshot jsonb not null default '{}'::jsonb,
 primary key(tenant_id,connection_id),
 foreign key(tenant_id,connection_id) references public.connections(tenant_id,id) on delete cascade,
 check ((status='ok' and observed_at is not null) or status<>'ok')
);
alter table public.ai_usage_settings enable row level security;
alter table public.ai_usage_snapshots enable row level security;
create policy tenant_isolation on public.ai_usage_settings for all to farm_app using(tenant_id=(select public.farm_tenant_id())) with check(tenant_id=(select public.farm_tenant_id()));
create policy tenant_isolation on public.ai_usage_snapshots for all to farm_app using(tenant_id=(select public.farm_tenant_id())) with check(tenant_id=(select public.farm_tenant_id()));
grant select,insert,update,delete on public.ai_usage_settings,public.ai_usage_snapshots to farm_app;
grant all on public.ai_usage_settings,public.ai_usage_snapshots to farm_system;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(
        "drop table public.ai_usage_snapshots; drop table public.ai_usage_settings; alter table public.connections drop constraint connections_tenant_id_id_key;"
    )
