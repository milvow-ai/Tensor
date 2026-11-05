"""Durable AI inputs and single-child recovery lineage.

Revision ID: 0021
Revises: 0020
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = r"""
alter table public.ai_jobs add constraint ai_jobs_tenant_id_id_key unique (tenant_id, id);
alter table public.ai_jobs add column recovery_parent_id uuid;
alter table public.ai_jobs add column recovery_depth integer not null default 0
  check (recovery_depth between 0 and 5);
alter table public.ai_jobs add column input_sha256 text;
alter table public.ai_jobs add column recovery_approval_actor text;
alter table public.ai_jobs add constraint ai_jobs_recovery_parent_fk
  foreign key (tenant_id, recovery_parent_id) references public.ai_jobs(tenant_id, id);
create unique index ai_jobs_one_recovery_child on public.ai_jobs(tenant_id, recovery_parent_id)
  where recovery_parent_id is not null;
create table public.ai_job_inputs (
  tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
  job_id uuid not null,
  payload bytea not null check (octet_length(payload) <= 4000000),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz not null default now(),
  primary key (tenant_id, job_id),
  foreign key (tenant_id, job_id) references public.ai_jobs(tenant_id, id) on delete cascade
);
alter table public.ai_job_inputs enable row level security;
create policy tenant_isolation on public.ai_job_inputs for all to farm_app
  using (tenant_id = (select public.farm_tenant_id()))
  with check (tenant_id = (select public.farm_tenant_id()));
grant select, insert, delete on public.ai_job_inputs to farm_app;
grant all on public.ai_job_inputs to farm_system;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("drop table public.ai_job_inputs")
    op.execute("alter table public.ai_jobs drop constraint ai_jobs_recovery_parent_fk")
    op.execute("drop index public.ai_jobs_one_recovery_child")
    op.execute(
        "alter table public.ai_jobs drop column recovery_parent_id, drop column recovery_depth, "
        "drop column input_sha256, drop column recovery_approval_actor, "
        "drop constraint ai_jobs_tenant_id_id_key"
    )
