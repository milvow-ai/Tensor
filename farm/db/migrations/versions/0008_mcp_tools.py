"""MCP pass-through: ``mcp_tools``, the cached tool catalogue of every MCP server the Farm fronts (OPEN1).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05 00:00:00.000000

One row per tool the server listed (``farm mcp sync``), after the provider's allow / deny globs:

* ``description`` / ``input_schema`` / ``output_schema`` / ``annotations`` are the server's own values, kept
  as queryable columns;
* ``definition`` is the complete ``tools/list`` entry as the server returned it (title, icons, ``_meta``,
  execution, ...), which is what the gateway re-publishes, so a tool reaches the AI exactly as the server
  described it;
* ``schema_hash`` is the sha256 of ``definition``: a changed hash on the next sync is a changed tool.

The table is owner-readable through the Data API like every other table (policy pattern of 0002) and is
written by the Farm process only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
create table public.mcp_tools (
  workspace_id  uuid not null default '00000000-0000-0000-0000-000000000001',
  provider      text not null references public.providers (id) on delete cascade,
  name          text not null,
  description   text not null default '',
  input_schema  jsonb not null,
  output_schema jsonb,
  annotations   jsonb,
  definition    jsonb not null,
  schema_hash   text not null,
  synced_at     timestamptz not null default now(),
  primary key (workspace_id, provider, name)
);

alter table public.mcp_tools enable row level security;

do $do$
begin
  if exists (select 1 from pg_namespace where nspname = 'auth') then
    drop policy if exists owner_select_mcp_tools on public.mcp_tools;
    create policy owner_select_mcp_tools on public.mcp_tools for select to authenticated
      using ((select public.farm_is_owner()));
    grant select on public.mcp_tools to authenticated;
  end if;
end $do$;
"""

DOWNGRADE_SQL = """
drop table if exists public.mcp_tools cascade;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
