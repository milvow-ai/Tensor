import json
import logging
import os
import re
import sys
from collections.abc import Coroutine
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import psycopg
import structlog
import typer
import yaml
from alembic import command
from alembic.config import Config

from farm import __version__
from farm.db.pool import get_db_url, is_local_db, run
from farm.registry import RegistryError, export_json_schema, load_registry

if TYPE_CHECKING:
    from farm.context import FarmContext
    from farm.resources.reports import PoolCapacity

app = typer.Typer()
db_app = typer.Typer(help="Database: local Postgres, migrations, checks.")
app.add_typer(db_app, name="db")
registry_app = typer.Typer(help="Registry: seed the database from YAML, export it back, print the schema.")
app.add_typer(registry_app, name="registry")

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "db" / "alembic.ini"
DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent.parent / "config" / "registry.yaml"

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


# --- running the Farm (M1c) -------------------------------------------------------------------------------


def configure_logging() -> None:
    """JSON log lines on stderr: stdout is the command's output and, for ``serve``, the MCP stream."""
    level = getattr(logging, os.environ.get("FARM_LOG_LEVEL", "INFO").upper(), logging.INFO)
    # No force=True: if the host process already set up logging (a test runner, an embedding app) keep it.
    logging.basicConfig(stream=sys.stderr, level=level, format="%(levelname)s %(name)s: %(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # sys.stderr is looked up per logger, not captured once: swapping it later never leaves a dead stream.
        logger_factory=lambda *_: structlog.PrintLogger(file=sys.stderr),
        cache_logger_on_first_use=False,
    )


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=code)


def _run_farm[T](work: Coroutine[Any, Any, T]) -> T:
    """Run ``work`` on the selector event loop psycopg needs; failures a person can fix get one clear line."""
    configure_logging()
    try:
        return run(work)
    except RegistryError as exc:
        raise _fail(str(exc)) from None
    except psycopg.errors.UndefinedTable:
        raise _fail("the database has no Farm schema yet: run `farm db migrate`") from None
    except psycopg.OperationalError as exc:
        raise _fail(f"cannot reach the database: {_safe(exc)}") from None


async def _context(local: bool) -> "FarmContext":
    from farm.context import build_context

    return await build_context(get_db_url(force_local=True) if local else None)


@app.command()
def serve(local: LocalOption = False) -> None:
    """Run the Harness Farm MCP server on stdio (what Claude and Hermes connect to)."""

    async def main() -> None:
        from farm.gateway.server import build_server

        ctx = await _context(local)
        try:
            server = await build_server(ctx)
            await server.run_async(transport="stdio", show_banner=False)
        finally:
            await ctx.aclose()

    _run_farm(main())


@registry_app.command(name="sync")
def registry_sync(
    path: Annotated[Path, typer.Argument(help="Registry YAML to apply.")] = DEFAULT_REGISTRY,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would change; write nothing.")
    ] = False,
    local: LocalOption = False,
) -> None:
    """Apply the registry file to the database (creates or updates what it lists; every change is audited)."""
    from farm.registry.sync import sync_registry

    try:
        registry = load_registry(path)
    except RegistryError as exc:
        raise _fail(str(exc)) from None

    async def main() -> None:
        ctx = await _context(local)
        try:
            report = await sync_registry(ctx.pool, registry, dry_run=dry_run)
        finally:
            await ctx.aclose()
        verb = "would change" if dry_run else "changed"
        typer.echo(f"registry {path.name}: {verb} {report.changes} row(s)")
        for label, bucket in (
            ("created", report.created),
            ("updated", report.updated),
            ("deleted", report.deleted),
        ):
            if bucket:
                typer.echo(f"  {label}: " + ", ".join(f"{table} {n}" for table, n in sorted(bucket.items())))
        if report.changes == 0:
            typer.echo("  the database already matches the file")
        for table, ids in report.orphans.items():
            typer.echo(
                f"  in the database but not in the file (kept, they own history): {table}: {', '.join(ids)}"
            )
        if dry_run:
            typer.echo("  dry run: nothing was written")

    _run_farm(main())


@registry_app.command(name="export")
def registry_export(
    path: Annotated[Path | None, typer.Argument(help="Write here instead of stdout.")] = None,
    local: LocalOption = False,
) -> None:
    """Write the registry as the database holds it (YAML); exporting and syncing it back is a no-op."""
    from farm.registry.sync import export_registry

    async def main() -> str:
        ctx = await _context(local)
        try:
            registry = await export_registry(ctx.pool)
        finally:
            await ctx.aclose()
        return yaml.safe_dump(registry.model_dump(mode="json"), sort_keys=False, allow_unicode=True)

    text = _run_farm(main())
    if path is None:
        typer.echo(text, nl=False)
    else:
        path.write_text(text, encoding="utf-8")
        typer.echo(f"registry exported to {path}")


@registry_app.command(name="schema")
def registry_schema() -> None:
    """Print the registry's JSON Schema (the Console builds its forms from it): ``... schema > file``."""
    typer.echo(json.dumps(export_json_schema(), indent=2))


@app.command()
def call(
    capability: Annotated[str, typer.Argument(help="Capability name, e.g. verify_email.")],
    params: Annotated[str, typer.Option("--params", help="Input as a JSON object.")] = "{}",
    strategy: Annotated[str | None, typer.Option(help="Strategy for this call.")] = None,
    pin: Annotated[str | None, typer.Option(help="Force this connection id.")] = None,
    caller: Annotated[str, typer.Option(help="Recorded as the caller of the run.")] = "cli",
    local: LocalOption = False,
) -> None:
    """Call a capability through the router (the path the MCP tools take) and print the envelope as JSON."""
    try:
        arguments = json.loads(params)
    except json.JSONDecodeError as exc:
        raise _fail(f"--params is not valid JSON: {exc.msg}", code=2) from None
    if not isinstance(arguments, dict):
        raise _fail("--params must be a JSON object", code=2)

    async def main() -> bool:
        from farm.resources.router import UnknownCapability, route

        ctx = await _context(local)
        try:
            outcome = await route(ctx, capability, arguments, strategy=strategy, pin=pin, caller=caller)
        except UnknownCapability as exc:
            raise _fail(str(exc)) from None
        finally:
            await ctx.aclose()
        typer.echo(json.dumps(outcome.envelope(), indent=2))
        return outcome.ok

    if not _run_farm(main()):
        raise typer.Exit(code=1)


@app.command()
def status(
    capability: Annotated[
        str | None, typer.Argument(help="Only the pools on this capability's route.")
    ] = None,
    local: LocalOption = False,
) -> None:
    """Pools and connections: state, circuit, cooldown, what is left of every unit and when it resets."""
    from farm.resources.reports import capacity_report

    async def main() -> "list[PoolCapacity]":
        ctx = await _context(local)
        try:
            return await capacity_report(ctx.pool, ctx.clock(), capability)
        finally:
            await ctx.aclose()

    pools = _run_farm(main())
    typer.echo(format_status(pools) if pools else "no pools: run `farm registry sync`")


def _when(at: datetime | None) -> str:
    return "-" if at is None else at.strftime("%Y-%m-%d %H:%MZ")


def format_status(pools: "list[PoolCapacity]") -> str:
    """The ``farm status`` table: a row per unit (pool, connection, state on the first row)."""
    rows: list[list[str]] = [["POOL", "CONNECTION", "STATE", "UNIT", "USED/LIMIT", "REMAINING", "RESETS"]]
    for pool in pools:
        pool_label = pool.provider_id if pool.enabled else f"{pool.provider_id} (disabled)"
        if not pool.connections:
            rows.append([pool_label, "-", "-", "-", "-", "-", "-"])
        for connection in pool.connections:
            state = connection.status
            if connection.unavailable_reason and connection.unavailable_reason != connection.status:
                state += f" [{connection.unavailable_reason} until {_when(connection.cooldown_until)}]"
            head = [pool_label, connection.id, state]
            for unit in connection.units:
                limit = "unlimited" if unit.limit is None else f"{unit.limit:g}"
                remaining = "-" if unit.remaining is None else f"{unit.remaining:g}"
                rows.append(
                    [
                        *head,
                        unit.unit,
                        f"{unit.used + unit.reserved:g}/{limit}",
                        remaining,
                        _when(unit.next_reset_at),
                    ]
                )
                head = ["", "", ""]
                pool_label = ""
            if not connection.units:
                rows.append([*head, "-", "-", "-", "-"])
                pool_label = ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows)
