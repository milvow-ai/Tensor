"""A completion that cannot be reconciled must not be automatically replayed."""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

KINDS = "'limit','auth','timeout','crash','bad_request','cancelled','farm_restart','account_unavailable'"


def upgrade() -> None:
    op.execute("alter table public.ai_jobs drop constraint ai_jobs_error_kind_check")
    op.execute(f"alter table public.ai_jobs add constraint ai_jobs_error_kind_check check (error_kind in ({KINDS},'outcome_unknown'))")


def downgrade() -> None:
    op.execute("update public.ai_jobs set error_kind='crash' where error_kind='outcome_unknown'")
    op.execute("alter table public.ai_jobs drop constraint ai_jobs_error_kind_check")
    op.execute(f"alter table public.ai_jobs add constraint ai_jobs_error_kind_check check (error_kind in ({KINDS}))")
