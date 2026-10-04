"""Alembic environment.

The database URL comes from ``config.attributes["db_url"]`` (set by ``farm db migrate`` and the test
fixtures) and falls back to ``farm.db.pool.get_db_url()``. The URL goes straight to psycopg through
SQLAlchemy's ``creator`` hook, so passwords with special characters need no escaping and nothing is parsed
or logged by SQLAlchemy.
"""

from logging.config import fileConfig

import psycopg
from alembic import context
from sqlalchemy import create_engine, pool

from farm.db.pool import get_db_url

config = context.config

# In-process callers (CLI, tests) set configure_logger=False so existing loggers are left alone.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None  # raw-SQL revisions, no autogenerate


def _db_url() -> str:
    url = config.attributes.get("db_url")
    return str(url) if url else get_db_url()


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting (``alembic upgrade head --sql``)."""
    context.configure(
        url="postgresql+psycopg://",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _db_url()
    engine = create_engine(
        "postgresql+psycopg://",
        creator=lambda: psycopg.connect(url),
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
