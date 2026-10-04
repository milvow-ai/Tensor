"""Farm Console views (v_connection_status, v_pool_overview, v_spend_month, v_capability_capacity, v_recent_runs).

Wraps console/sql/views.sql.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 00:00:02.000000
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEWS_SQL_PATH = Path(__file__).resolve().parents[4] / "console" / "sql" / "views.sql"

DOWNGRADE_SQL = """
drop view if exists public.v_recent_runs cascade;
drop view if exists public.v_capability_capacity cascade;
drop view if exists public.v_spend_month cascade;
drop view if exists public.v_pool_overview cascade;
drop view if exists public.v_connection_status cascade;
"""


def upgrade() -> None:
    sql = _VIEWS_SQL_PATH.read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
