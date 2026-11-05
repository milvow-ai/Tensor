"""Durable checkpoints of automatic same-provider failover.

Before a failed attempt's job is handed to another account, the exact task the next attempt runs (the original
input, or a context pack of original input + history + partial output) is committed here, in the same
transaction that requeues the job. ``ai_job_inputs`` stays immutable.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = r"""
create table public.ai_job_checkpoints (
  tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
  job_id uuid not null,
  attempt integer not null check (attempt between 2 and 5),
  from_account text not null,
  to_account text not null,
  payload bytea not null check (octet_length(payload) <= 4000000),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  partial bytea check (partial is null or octet_length(partial) <= 400000),
  partial_chars integer check (partial_chars is null or partial_chars >= 0),
  created_at timestamptz not null default now(),
  primary key (tenant_id, job_id, attempt),
  foreign key (tenant_id, job_id) references public.ai_jobs(tenant_id, id) on delete cascade
);
alter table public.ai_job_checkpoints enable row level security;
create policy tenant_isolation on public.ai_job_checkpoints for all to farm_app
  using (tenant_id = (select public.farm_tenant_id()))
  with check (tenant_id = (select public.farm_tenant_id()));
grant select, insert, delete on public.ai_job_checkpoints to farm_app;
grant all on public.ai_job_checkpoints to farm_system;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute("drop table public.ai_job_checkpoints")
