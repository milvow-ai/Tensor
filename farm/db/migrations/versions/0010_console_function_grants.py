"""Console function grants on Supabase.

* ``farm_period_start`` is a pure helper (computes a period start, reads no table). The Console views
  ``v_connection_status`` and ``v_renewals`` are ``security_invoker`` and call it, so the signed-in owner
  (role ``authenticated``) needs EXECUTE on it. 0002 revoked it with the ledger functions; give it back.
* ``farm_reactivate_due`` (0005) changes connection rows and was left callable over RPC. Server-only, like
  the other data-changing ``farm_*`` functions in 0002.

Local (non-Supabase) databases have no ``anon``/``authenticated`` roles; then this migration does nothing.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-05 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    grant execute on function public.farm_period_start(text, int, timestamptz) to authenticated;
    revoke all on function public.farm_reactivate_due(timestamptz) from public;
    revoke all on function public.farm_reactivate_due(timestamptz) from authenticated;
  end if;
  if exists (select 1 from pg_roles where rolname = 'anon') then
    revoke all on function public.farm_reactivate_due(timestamptz) from anon;
  end if;
end $$;
"""

DOWNGRADE_SQL = """
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    revoke all on function public.farm_period_start(text, int, timestamptz) from authenticated;
    grant execute on function public.farm_reactivate_due(timestamptz) to public;
  end if;
end $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
