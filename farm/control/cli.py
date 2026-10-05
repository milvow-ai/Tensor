import json
import logging
import os
import re
import sys
from collections.abc import Coroutine
from datetime import UTC, datetime
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
    from uuid import UUID

    from farm.ai.jobs import JobRecord
    from farm.context import FarmContext
    from farm.resources.reports import PoolCapacity

app = typer.Typer()
db_app = typer.Typer(help="Database: local Postgres, migrations, checks.")
app.add_typer(db_app, name="db")
registry_app = typer.Typer(help="Registry: seed the database from YAML, export it back, print the schema.")
app.add_typer(registry_app, name="registry")

commands_app = typer.Typer(help="Command contracts and schemas.")
app.add_typer(commands_app, name="commands")

ai_app = typer.Typer(help="AI pool: Claude, Codex, Gemini/Agy, Hermes accounts.")
app.add_typer(ai_app, name="ai")


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


def format_ai_list(data: dict[str, Any]) -> str:
    rows: list[list[str]] = [["AI", "ACCOUNT", "STATUS", "MODELS", "RESET / COOLDOWN", "CALLS TODAY", "JOBS"]]
    ais = data.get("ais", [])
    if not ais:
        return "no AI pools found: run `farm registry sync`"
    for ai in ais:
        ai_name = ai.get("id", "")
        accounts = ai.get("accounts", [])
        if not accounts:
            rows.append([ai_name, "-", "-", "-", "-", "-", "-"])
            continue
        first = True
        for acc in accounts:
            status = acc.get("status", "")
            reset_time = acc.get("reset_at") or acc.get("cooldown_until")
            reset_str = "-" if not reset_time else str(reset_time)[:16].replace("T", " ") + "Z"
            if status == "exhausted" and reset_time:
                status_str = f"exhausted [resets {reset_str}]"
            elif acc.get("circuit") == "open":
                status_str = f"circuit_open [{reset_str}]"
            elif status == "needs_login":
                status_str = "needs_login"
            else:
                status_str = status
            models = ", ".join(acc.get("models", [])) or "-"
            calls = str(acc.get("todays_calls", 0))
            queued = int(acc.get("queued_jobs", 0))
            jobs = f"{int(acc.get('active_jobs', 0))}/{int(acc.get('max_parallel', 1))}" + (
                f" +{queued} queued" if queued else ""
            )
            rows.append([
                ai_name if first else "",
                acc.get("id", ""),
                status_str,
                models,
                reset_str,
                calls,
                jobs,
            ])
            first = False
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows)


@ai_app.command(name="list")
def ai_list(local: LocalOption = False) -> None:
    """List all AI accounts with their status, models, reset times, and today's calls."""
    from farm.ai.accounts import list_ai_accounts

    async def main() -> dict[str, Any]:
        ctx = await _context(local)
        try:
            return await list_ai_accounts(ctx.pool, ctx.clock())
        finally:
            await ctx.aclose()

    data = _run_farm(main())
    typer.echo(format_ai_list(data))


@ai_app.command(name="test")
def ai_test(
    connection: Annotated[str, typer.Argument(help="Connection ID to test, e.g. agy-01, hermes-01.")],
    local: LocalOption = False,
) -> None:
    """Run a tiny test call on an AI connection."""
    from farm.resources.router import route

    async def main() -> bool:
        ctx = await _context(local)
        try:
            outcome = await route(
                ctx,
                "ask_ai",
                {"task": "Respond with only the word PONG", "mode": "answer"},
                pin=connection,
                caller="cli",
            )
        finally:
            await ctx.aclose()

        if outcome.ok and outcome.result:
            text = str(outcome.result.get("text", "")).strip()
            cost = outcome.cost.usd if outcome.cost else 0.0
            typer.echo(f"OK: {connection} answered: {text} (cost: ${cost:.6f})")
            return True
        else:
            err = outcome.error.message if outcome.error else "unknown error"
            typer.echo(f"FAIL: {connection}: {err}", err=True)
            return False

    if not _run_farm(main()):
        raise typer.Exit(code=1)


def _stamp(at: datetime | None) -> str:
    """A moment in UTC (the database hands datetimes back in its session time zone)."""
    return "-" if at is None else at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _since(at: datetime | None, now: datetime) -> str:
    if at is None:
        return "-"
    seconds = max(0, int((now - at).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def format_job_table(jobs: "list[JobRecord]", now: datetime) -> str:
    """The ``farm ai jobs`` table: one row per job, newest first."""
    rows: list[list[str]] = [["JOB", "STATE", "AI/ACCOUNT", "TURN", "AGE", "TOKENS", "COST", "ERROR"]]
    for job in jobs:
        error = "-"
        if job.error_kind is not None:
            error = job.error_kind + (f": {job.error_message[:48]}" if job.error_message else "")
        rows.append(
            [
                str(job.id),
                job.state,
                f"{job.ai}/{job.account}",
                str(job.turn),
                _since(job.created_at, now),
                "-" if job.tokens is None else str(job.tokens),
                f"${job.cost_usd:.4f}" + ("~" if job.cost_estimated and job.finished else ""),
                error,
            ]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows)


def _parse_job_id(value: str) -> "UUID":
    from farm.capabilities.schemas import parse_uuid

    try:
        return parse_uuid(value, "job id")
    except ValueError as exc:
        raise _fail(str(exc), code=2) from None


@ai_app.command(name="jobs")
def ai_jobs(
    state: Annotated[
        str | None,
        typer.Option(help="Only jobs in this state: queued, running, succeeded, failed or cancelled."),
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=500, help="How many jobs to show.")] = 20,
    local: LocalOption = False,
) -> None:
    """List AI jobs, newest first: state, account, turn, age, tokens, cost (~ = estimated) and error."""
    from farm.ai.jobs import JobStore
    from farm.capabilities.schemas import TERMINAL_STATES

    if state is not None and state not in {"queued", "running", *TERMINAL_STATES}:
        raise _fail(f"unknown state '{state}'", code=2)

    async def main() -> "tuple[list[JobRecord], datetime]":
        ctx = await _context(local)
        try:
            return await JobStore(ctx.pool).newest(state=state, limit=limit), ctx.clock()
        finally:
            await ctx.aclose()

    jobs, now = _run_farm(main())
    typer.echo(format_job_table(jobs, now) if jobs else "no AI jobs")


@ai_app.command(name="show")
def ai_show(
    job_id: Annotated[str, typer.Argument(help="A job id from `farm ai jobs` or ai_start.")],
    full: Annotated[bool, typer.Option("--full", help="Print the whole result text, not a preview.")] = False,
    local: LocalOption = False,
) -> None:
    """Show one AI job: where it ran, what it cost, every attempt, why it failed, and its result."""
    import asyncio

    from farm.ai import results
    from farm.ai.jobs import JobStore

    wanted = _parse_job_id(job_id)

    async def main() -> "tuple[JobRecord | None, str | None]":
        ctx = await _context(local)
        try:
            job = await JobStore(ctx.pool).get(wanted)
            text = None
            if job is not None and job.result_path:
                text = await asyncio.to_thread(results.read_result, job.result_path)
            return job, text
        finally:
            await ctx.aclose()

    job, text = _run_farm(main())
    if job is None:
        raise _fail(f"there is no job {job_id}")
    typer.echo(f"job          {job.id}")
    typer.echo(f"conversation {job.conversation_id} (turn {job.turn})")
    typer.echo(f"state        {job.state}")
    typer.echo(f"worker       {job.ai} / {job.account}  model={job.model or '-'}  mode={job.mode}")
    typer.echo(f"created      {_stamp(job.created_at)}   finished {_stamp(job.finished_at)}")
    estimate = " (estimated)" if job.cost_estimated else " (reported)"
    typer.echo(f"tokens/cost  {job.tokens if job.tokens is not None else '-'}  ${job.cost_usd:.6f}{estimate}")
    typer.echo(f"session      {job.native_session_id or '-'}")
    for attempt in job.attempts:
        detail = f"{attempt.get('kind')}: {attempt.get('message')}" if attempt.get("kind") else ""
        line = f"attempt {attempt.get('n')}    {attempt.get('account')} {attempt.get('outcome')} {detail}"
        typer.echo(line.rstrip())
    if job.error_kind is not None:
        back = f" (retry at {_stamp(job.retry_at)})" if job.retry_at else ""
        typer.echo(f"error        {job.error_kind}{back}: {job.error_message or ''}")
    if job.files_changed:
        typer.echo("files        " + ", ".join(job.files_changed))
    if job.result_path:
        typer.echo(f"result       {job.result_path} ({job.result_chars} chars)")
    if text is not None:
        shown = text if full else results.preview(text)[:2000]
        cut = "" if full or len(shown) == len(text) else f" (first {len(shown)} chars)"
        typer.echo(f"--- result{cut} ---")
        typer.echo(shown)


@ai_app.command(name="cancel")
def ai_cancel(
    job_id: Annotated[str, typer.Argument(help="A job id from `farm ai jobs`.")],
    local: LocalOption = False,
) -> None:
    """Stop an AI job (a running Farm kills its process tree within seconds of the request)."""
    from farm.ai.failures import AiRequestError
    from farm.ai.jobs import JobManager

    wanted = _parse_job_id(job_id)

    async def main() -> "tuple[str, str, bool]":
        ctx = await _context(local)
        try:
            outcome = await JobManager(ctx).cancel(wanted)
        except AiRequestError as exc:
            raise _fail(exc.message) from None
        finally:
            await ctx.aclose()
        return outcome.job.state, str(outcome.job.id), outcome.cancelled

    state, ident, cancelled = _run_farm(main())
    if cancelled:
        typer.echo(f"cancelled {ident}")
    elif state in ("queued", "running"):
        raise _fail(
            f"cancel requested for {ident} but it is still {state}: the Farm process that runs it did not "
            "acknowledge within 15 s (is it running?)"
        )
    else:
        typer.echo(f"job {ident} had already finished ({state})")


@ai_app.command(name="login")
def ai_login(
    connection: Annotated[str, typer.Argument(help="Connection ID to log in, e.g. claude-02, codex-01.")],
    local: LocalOption = False,
) -> None:
    """Print and run the exact interactive login for that account."""
    import subprocess

    from farm.settings import data_dir

    async def get_conn_info() -> dict[str, Any]:
        ctx = await _context(local)
        try:
            async with ctx.pool.connection() as conn:
                cur = await conn.execute(
                    "select c.id, c.provider_id, c.meta from public.connections c where c.id = %s",
                    (connection,),
                )
                row = await cur.fetchone()
                if row is None:
                    raise _fail(f"connection '{connection}' not found in database")
                return {"id": row[0], "provider_id": row[1], "meta": row[2] or {}}
        finally:
            await ctx.aclose()

    conn_info = _run_farm(get_conn_info())
    meta = conn_info["meta"]
    cli_name = str(meta.get("cli") or conn_info["provider_id"]).lower()

    config_dir_str = meta.get("home") or meta.get("config_dir")
    if not config_dir_str:
        config_dir = data_dir() / "ai" / connection
    else:
        config_dir = Path(config_dir_str)
    config_dir.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    if cli_name == "claude":
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        typer.echo(f"Account: {connection} (Claude)")
        typer.echo(f"Config directory: {config_dir}")
        typer.echo(f"Command: CLAUDE_CONFIG_DIR={config_dir} claude")
        typer.echo("Starting interactive Claude Code... type /login to complete authentication.")
        subprocess.run(["claude"], env=env)
    elif cli_name == "codex":
        env["CODEX_HOME"] = str(config_dir)
        typer.echo(f"Account: {connection} (Codex)")
        typer.echo(f"Config directory: {config_dir}")
        typer.echo(f"Command: CODEX_HOME={config_dir} codex login")
        subprocess.run(["codex", "login"], env=env)
    elif cli_name in ("agy", "gemini"):
        typer.echo(f"Account: {connection} (Antigravity)")
        if meta.get("home"):
            # agy has no config-dir flag or variable; it keeps its login under the user's home directory, so
            # this account gets its own home (the executor sets the same variables when it runs jobs).
            env["USERPROFILE"] = str(config_dir)
            env["HOME"] = str(config_dir)
            typer.echo(f"Home directory: {config_dir}")
            typer.echo(f"Command: USERPROFILE={config_dir} HOME={config_dir} agy")
        else:
            typer.echo("No meta.home on this connection: Antigravity uses the global login on this machine.")
            typer.echo("Command: agy")
        subprocess.run(["agy"], env=env)
    elif cli_name == "hermes":
        profile = meta.get("profile", "farm-agent")
        typer.echo(f"Account: {connection} (Hermes)")
        typer.echo(f"Profile: {profile}")
        typer.echo(f"Command: hermes -p {profile}")
        subprocess.run(["hermes", "-p", str(profile)], env=env)
    else:
        typer.echo(f"Unknown CLI driver '{cli_name}' for connection {connection}")

