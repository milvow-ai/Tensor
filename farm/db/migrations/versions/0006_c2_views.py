"""Farm Console C2 views (v_spend_daily, v_cost_per_result, v_renewals, v_idle_paid, v_run_detail).

Wraps console/sql/views_c2.sql.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04 00:00:03.000000
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEWS_SQL_PATH = Path(__file__).resolve().parents[4] / "console" / "sql" / "views_c2.sql"

DOWNGRADE_SQL = """
drop view if exists public.v_run_detail cascade;
drop view if exists public.v_idle_paid cascade;
drop view if exists public.v_renewals cascade;
drop view if exists public.v_cost_per_result cascade;
drop view if exists public.v_spend_daily cascade;
"""


def upgrade() -> None:
    sql = _VIEWS_SQL_PATH.read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
