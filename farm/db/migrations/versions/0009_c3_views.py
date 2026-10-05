"""Farm Console C3 views (v_routes, v_facts, v_evidence).

Wraps console/sql/views_c3.sql.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-05 00:00:00.000000
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEWS_SQL_PATH = Path(__file__).resolve().parents[4] / "console" / "sql" / "views_c3.sql"

DOWNGRADE_SQL = """
drop view if exists public.v_evidence cascade;
drop view if exists public.v_facts cascade;
drop view if exists public.v_routes cascade;
"""


def upgrade() -> None:
    sql = _VIEWS_SQL_PATH.read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
