"""Person-private work inside an explicitly shared resource gateway."""

from collections.abc import Sequence

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIRECT = {
    "ai_jobs": "operator_user_id",
    "runs": "operator_user_id",
    "ai_conversations": "operator_user_id",
    "capability_requests": "operator_user_id",
}
CHILD = {
    "ai_job_inputs": ("ai_jobs", "job_id"),
    "ai_job_checkpoints": ("ai_jobs", "job_id"),
    "run_events": ("runs", "run_id"),
}


def upgrade() -> None:
    op.execute("""
    create function public.farm_actor_id() returns uuid language sql stable as
      $$ select nullif(current_setting('app.user_id',true),'')::uuid $$;
    create function public.farm_private_visible(owner_id uuid) returns boolean
    language plpgsql stable security definer set search_path='' as $body$
    declare actor uuid;
    begin
      if current_setting('role',true) in ('authenticated','anon') then
        if not exists(select 1 from pg_namespace where nspname='auth') then return false; end if;
        execute 'select id from public.users where auth_subject=auth.uid()::text' into actor;
        return actor is not null and owner_id=actor;
      end if;
      actor := public.farm_actor_id();
      return actor is null or owner_id=actor;
    end $body$;
    revoke all on function public.farm_private_visible(uuid) from public;
    grant execute on function public.farm_private_visible(uuid), public.farm_actor_id() to farm_app,farm_system;
    create function public.farm_login_visible(actor_email text) returns boolean
    language sql stable security definer set search_path='' as $body$
      select public.farm_private_visible((select id from public.users where lower(email)=lower(actor_email)))
    $body$;
    revoke all on function public.farm_login_visible(text) from public;
    grant execute on function public.farm_login_visible(text) to farm_app,farm_system;
    alter table public.ai_conversations add column operator_user_id uuid
      default public.farm_actor_id() references public.users(id) on delete set null;
    alter table public.capability_requests add column operator_user_id uuid
      default public.farm_actor_id() references public.users(id) on delete set null;
    do $b$ begin
      if exists(select 1 from pg_roles where rolname='authenticated') then
        grant execute on function public.farm_private_visible(uuid) to authenticated;
        grant execute on function public.farm_login_visible(text) to authenticated;
      end if;
    end $b$;
    """)
    expressions = {table: f"public.farm_private_visible({column})" for table, column in DIRECT.items()}
    expressions.update(
        {
            table: f"exists(select 1 from public.{parent} p where p.id={table}.{key})"
            for table, (parent, key) in CHILD.items()
        }
    )
    expressions["login_sessions"] = "public.farm_login_visible(login_sessions.created_by)"
    for table, expression in expressions.items():
        op.execute(
            f"create policy person_private_work on public.{table} as restrictive to farm_app "
            f"using ({expression}) with check ({expression})"
        )
        op.execute(f"""do $b$ begin
          if exists(select 1 from pg_roles where rolname='authenticated') then
            create policy person_private_work_auth on public.{table} as restrictive for select to authenticated
              using ({expression});
          end if; end $b$;""")


def downgrade() -> None:
    for table in (*DIRECT, *CHILD, "login_sessions"):
        op.execute(f"drop policy if exists person_private_work on public.{table}")
        op.execute(f"drop policy if exists person_private_work_auth on public.{table}")
    op.execute("alter table public.ai_conversations drop column operator_user_id")
    op.execute("alter table public.capability_requests drop column operator_user_id")
    op.execute("drop function public.farm_login_visible(text)")
    op.execute("drop function public.farm_private_visible(uuid)")
    op.execute("drop function public.farm_actor_id()")
