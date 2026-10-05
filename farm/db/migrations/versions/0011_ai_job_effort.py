"""Add effort column to ai_jobs.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
alter table public.ai_jobs
  add column effort text check (effort is null or effort in ('low', 'medium', 'high', 'xhigh', 'max'));
"""

DOWNGRADE_SQL = """
alter table public.ai_jobs drop column if exists effort;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
