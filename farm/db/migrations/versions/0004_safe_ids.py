"""Safe IDs CHECK constraints on providers.id and connections.id.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04 00:00:03.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE_SQL = """
do $$
declare
    invalid_providers int;
    invalid_conns int;
begin
    select count(*) into invalid_providers
    from public.providers
    where id !~ '^[a-z0-9][a-z0-9_-]{0,62}$';

    if invalid_providers > 0 then
        raise exception 'public.providers contains % invalid id(s)', invalid_providers;
    end if;

    select count(*) into invalid_conns
    from public.connections
    where id !~ '^[a-z0-9][a-z0-9_-]{0,62}$';

    if invalid_conns > 0 then
        raise exception 'public.connections contains % invalid id(s)', invalid_conns;
    end if;
end $$;

alter table public.providers
    add constraint check_providers_id_slug
    check (id ~ '^[a-z0-9][a-z0-9_-]{0,62}$');

alter table public.connections
    add constraint check_connections_id_slug
    check (id ~ '^[a-z0-9][a-z0-9_-]{0,62}$');
"""

DOWNGRADE_SQL = """
alter table public.connections drop constraint if exists check_connections_id_slug;
alter table public.providers drop constraint if exists check_providers_id_slug;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
