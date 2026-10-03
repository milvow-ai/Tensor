import psycopg
import typer

from farm import __version__
from farm.settings import load_env, require

app = typer.Typer()
db_app = typer.Typer()
app.add_typer(db_app, name="db")


@app.command()
def version() -> None:
    print(__version__)


@db_app.command(name="check")
def db_check() -> None:
    try:
        load_env()
        db_url = require("SUPABASE_DB_URL")
        with psycopg.connect(db_url, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("select version()")
                row = cur.fetchone()
                ver = str(row[0]) if row else ""
                print(f"db ok: {ver[:40]}")
    except typer.Exit:
        raise
    except Exception as exc:
        print(f"db FAIL: {exc.__class__.__name__}")
        raise typer.Exit(code=1) from None
