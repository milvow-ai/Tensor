"""Let the trusted maintenance role work without managed Postgres BYPASSRLS."""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    do $b$ declare target record;
    begin
      for target in
        select c.relname as name from pg_class c join pg_namespace n on n.oid=c.relnamespace
        where n.nspname='public' and c.relkind in ('r','p') and c.relrowsecurity
          and (c.relname in ('alembic_version','tenants','users','farm_heartbeats') or exists (
            select 1 from pg_policies p where p.schemaname='public' and p.tablename=c.relname
              and p.policyname='tenant_isolation' and 'farm_app' = any(p.roles)
          ))
      loop
        execute format('create policy farm_maintenance on public.%I for all to farm_system '
                       'using (true) with check (true)', target.name);
      end loop;
    end $b$;
    """)


def downgrade() -> None:
    op.execute("""
    do $b$ declare target record;
    begin
      for target in select tablename from pg_policies
        where schemaname='public' and policyname='farm_maintenance'
      loop
        execute format('drop policy farm_maintenance on public.%I', target.tablename);
      end loop;
    end $b$;
    """)
