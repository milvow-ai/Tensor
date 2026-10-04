import re
from pathlib import Path
from typing import Annotated

import psycopg
import typer
from alembic import command
from alembic.config import Config

from farm import __version__
from farm.db.pool import get_db_url, is_local_db

app = typer.Typer()
db_app = typer.Typer(help="Database: local Postgres, migrations, checks.")
app.add_typer(db_app, name="db")
commands_app = typer.Typer(help="Command contracts and schemas.")
app.add_typer(commands_app, name="commands")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "db" / "alembic.ini"

LocalOption = Annotated[
    bool,
    typer.Option("--local", help="Ignore FARM_DB_URL / SUPABASE_DB_URL; use the embedded local Postgres."),
]


def _safe(exc: BaseException) -> str:
    """Exception text with any ``scheme://user:password@`` credentials masked."""
    text = f"{exc.__class__.__name__}: {exc}".strip()
    return re.sub(r"(\w+://[^:/@\s]*:)[^@\s]+@", r"\1***@", text)


def alembic_config(db_url: str) -> Config:
    """Alembic config for ``db_url`` (passed as an attribute: no ini interpolation of special characters)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["db_url"] = db_url
    cfg.attributes["configure_logger"] = False
    return cfg


@app.command()
def version() -> None:
    print(__version__)


@db_app.command(name="check")
def db_check(local: LocalOption = False) -> None:
    """Connect to the configured database and print its version."""
    try:
        with psycopg.connect(get_db_url(force_local=local), connect_timeout=10) as conn:
            row = conn.execute("select version()").fetchone()
            print("db ok:", row[0] if row else "unknown")
    except Exception as exc:
        print(f"db FAIL: {_safe(exc)}")
        raise typer.Exit(code=1) from None


@db_app.command(name="up")
def db_up() -> None:
    """Start (or reuse) the embedded local Postgres and print its port."""
    from farm.db.local import get_local_port

    try:
        print(f"local db ready on port {get_local_port()}")
    except Exception as exc:
        print(f"db up FAIL: {_safe(exc)}")
        raise typer.Exit(code=1) from None


@db_app.command(name="migrate")
def db_migrate(local: LocalOption = False) -> None:
    """Apply all migrations (alembic upgrade head) to the configured database."""
    try:
        command.upgrade(alembic_config(get_db_url(force_local=local)), "head")
        print("db migrate ok")
    except Exception as exc:
        print(f"db migrate FAIL: {_safe(exc)}")
        raise typer.Exit(code=1) from None


@db_app.command(name="reset-local")
def db_reset_local() -> None:
    """Drop and recreate the LOCAL embedded database (refuses when the configured URL is not local)."""
    from farm.db.local import get_local_port, reset_local

    if not is_local_db():
        print("db reset-local REFUSED: FARM_DB_URL / SUPABASE_DB_URL points to a non-local database")
        raise typer.Exit(code=1)
    try:
        reset_local()
        print(f"local db reset, ready on port {get_local_port()}")
    except Exception as exc:
        print(f"db reset-local FAIL: {_safe(exc)}")
        raise typer.Exit(code=1) from None


@commands_app.command(name="schema")
def commands_schema(
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Path to write the exported command schemas JSON."),
    ] = None,
) -> None:
    """Export JSON Schemas for all Farm commands."""
    import json

    from farm.control.commands import export_command_schemas

    schemas = export_command_schemas()
    payload = json.dumps(schemas, indent=2)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote command schemas to {out}")
    else:
        print(payload)
