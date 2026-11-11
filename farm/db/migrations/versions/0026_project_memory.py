"""Versioned project notes with tenant isolation and conflict-safe updates."""

from collections.abc import Sequence

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
    create table public.project_memory (
      tenant_id uuid not null default public.farm_tenant_id() references public.tenants(id),
      project text not null check (length(project) between 1 and 63),
      key text not null check (length(key) between 1 and 128),
      body text not null check (octet_length(body) <= 16384),
      version bigint not null default 1 check (version > 0),
      deleted boolean not null default false,
      updated_by uuid references public.users(id) on delete set null,
      updated_at timestamptz not null default now(),
      primary key (tenant_id, project, key)
    );
    alter table public.project_memory enable row level security;
    create policy tenant_isolation on public.project_memory to farm_app
      using (tenant_id = public.farm_tenant_id()) with check (tenant_id = public.farm_tenant_id());
    grant all on public.project_memory to farm_app, farm_system;
    do $body$ begin
      if exists(select 1 from pg_roles where rolname='authenticated')
        and to_regprocedure('public.farm_member_tenant_ids()') is not null then
        grant select on public.project_memory to authenticated;
        create policy member_select_project_memory on public.project_memory for select to authenticated
          using (tenant_id in (select public.farm_member_tenant_ids()));
      end if;
    end $body$;
    """)


def downgrade() -> None:
    op.execute("drop table public.project_memory")
