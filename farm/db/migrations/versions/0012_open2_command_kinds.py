"""Allow open2 and tool command kinds in farm_commands.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-05 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
alter table public.farm_commands
  drop constraint if exists farm_commands_kind_check;

alter table public.farm_commands
  add constraint farm_commands_kind_check check (kind in (
    'pause',
    'resume',
    'set_priority',
    'set_strategy',
    'set_budget',
    'add_connection',
    'update_connection',
    'remove_connection',
    'set_route',
    'test_connection',
    'ack_alert',
    'cancel_ai_job',
    'set_max_parallel',
    'set_mcp_tool_access',
    'sync_mcp_tools',
    'add_provider',
    'update_provider',
    'remove_provider'
  ));
"""

DOWNGRADE_SQL = """
alter table public.farm_commands
  drop constraint if exists farm_commands_kind_check;

alter table public.farm_commands
  add constraint farm_commands_kind_check check (kind in (
    'pause',
    'resume',
    'set_priority',
    'set_strategy',
    'set_budget',
    'add_connection',
    'update_connection',
    'remove_connection',
    'set_route',
    'test_connection',
    'ack_alert'
  ));
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
