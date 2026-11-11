"""Persist verified operator identity separately from caller labels and worker accounts."""

from collections.abc import Sequence

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    "id, tenant_id, conversation_id, turn, ai, account, model, resolved_model, effort as requested_effort, "
    "effective_effort, selection_source, mode, state, tokens, cost_usd, cost_estimated, duration_s, "
    "error_kind, created_at, started_at, finished_at, updated_at"
)


def view(extra: str) -> None:
    op.execute(
        "create or replace view public.v_ai_jobs with (security_invoker=true) as select "
        + COLUMNS
        + extra
        + " from public.ai_jobs"
    )
    op.execute("grant select on public.v_ai_jobs to farm_app,farm_system")
    op.execute("""do $b$ begin if exists(select 1 from pg_roles where rolname='authenticated') then
      grant select on public.v_ai_jobs to authenticated; end if; end $b$;""")


def upgrade() -> None:
    for table in ("ai_jobs", "runs"):
        op.execute(
            f"alter table public.{table} add column operator_user_id uuid references public.users(id) "
            "on delete set null, add column operator_email text, add column operator_client text"
        )
    view(", operator_user_id, operator_email, operator_client")


def downgrade() -> None:
    op.execute("drop view public.v_ai_jobs")
    for table in ("ai_jobs", "runs"):
        op.execute(
            f"alter table public.{table} drop column operator_user_id, "
            "drop column operator_email, drop column operator_client"
        )
    view("")
