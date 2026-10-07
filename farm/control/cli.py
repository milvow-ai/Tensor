import asyncio
import concurrent.futures
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

mcp_app = typer.Typer(help="MCP servers: import them from Claude / Codex, sync their tools, log accounts in.")
app.add_typer(mcp_app, name="mcp")

token_app = typer.Typer(help="Client tokens: create, list, and revoke tokens for HTTP MCP clients.")
app.add_typer(token_app, name="token")

alert_app = typer.Typer(help="Alerts: dispatch and check alerts.")
app.add_typer(alert_app, name="alert")

provider_app = typer.Typer(help="Manage providers: remove integrations.")
app.add_typer(provider_app, name="provider")

requests_app = typer.Typer(help="Integration requests: list, show, and resolve.")
app.add_typer(requests_app, name="requests")


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
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if in_loop:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(lambda: run(work)).result()
        return run(work)
    except RegistryError as exc:
        raise _fail(str(exc)) from None
    except psycopg.errors.UndefinedTable:
        raise _fail("the database has no Farm schema yet: run `farm db migrate`") from None
    except psycopg.OperationalError as exc:
        raise _fail(f"cannot reach the database: {_safe(exc)}") from None


async def _context(local: bool) -> "FarmContext":
    from farm.context import build_context
    from farm.executors.mcp.client import McpExecutor
    from farm.mcp.store import DbDirectory

    ctx = await build_context(get_db_url(force_local=True) if local else None)
    # build_context() does not wire the MCP executor yet; commands that route need it for MCP providers.
    ctx.executors.setdefault("mcp", McpExecutor(directory=DbDirectory(ctx.pool)))
    return ctx


def _mcp_exposure() -> tuple[int, list[str]]:
    """``settings.mcp_direct_limit`` / ``settings.mcp_pinned`` of the registry file (default: 40, none)."""
    try:
        settings = load_registry(DEFAULT_REGISTRY).settings
    except RegistryError as exc:
        log = structlog.get_logger("farm.cli")
        log.warning("mcp.settings_unreadable", registry=str(DEFAULT_REGISTRY), error=str(exc)[:200])
        return 40, []
    return settings.mcp_direct_limit, list(settings.mcp_pinned)


async def _refresh_mcp_catalogue(ctx: "FarmContext") -> None:
    """Re-list every MCP server's tools in the background while the server runs (never blocks, never raises).

    The tool list of a running ``farm serve`` is fixed at its start from the stored catalogue; what this finds
    is stored for the next start.
    """
    from farm.executors.mcp.client import McpExecutor
    from farm.mcp.sync import sync_all

    log = structlog.get_logger("farm.cli")
    executor = ctx.executors.get("mcp")
    if not isinstance(executor, McpExecutor):
        return
    try:
        results = await sync_all(ctx.pool, executor)
    except Exception as exc:  # a failing refresh must never take the server down
        log.error("mcp.refresh_failed", error=type(exc).__name__)
        return
    log.info(
        "mcp.refreshed",
        providers=len(results),
        failed=[r.provider for r in results if not r.ok],
        changed=[
            r.provider
            for r in results
            if r.diff is not None and (r.diff.added or r.diff.changed or r.diff.removed)
        ],
    )


@app.command()
def serve(local: LocalOption = False) -> None:
    """Run the Harness Farm MCP server on stdio (what Claude and Hermes connect to)."""

    async def main() -> None:
        import asyncio

        from farm.gateway.server import build_server

        ctx = await _context(local)
        refresh: asyncio.Task[None] | None = None
        try:
            limit, pinned = _mcp_exposure()
            server = await build_server(ctx, mcp_direct_limit=limit, mcp_pinned=pinned)
            refresh = asyncio.create_task(_refresh_mcp_catalogue(ctx))
            await server.run_async(transport="stdio", show_banner=False)
        finally:
            if refresh is not None:
                refresh.cancel()
                await asyncio.gather(refresh, return_exceptions=True)
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


@ai_app.command(name="add")
def ai_add(
    driver: Annotated[str, typer.Argument(help="AI CLI driver: claude, codex, gemini, or hermes.")],
    account_id: Annotated[str, typer.Argument(help="Account ID, e.g. claude-02.")],
    label: Annotated[str | None, typer.Option("--label", "-l", help="Display label.")] = None,
    model: Annotated[list[str] | None, typer.Option("--model", "-m", help="Allowed models.")] = None,
    max_parallel: Annotated[int, typer.Option("--max-parallel", help="Max parallel jobs.")] = 1,
    local: LocalOption = False,
) -> None:
    """Add a new AI CLI account (Claude, Codex, Gemini/agy, Hermes)."""
    from farm.control.commands import execute_provider_command

    driver_clean = driver.lower().strip()
    if driver_clean not in ("claude", "codex", "gemini", "agy", "hermes"):
        raise _fail(f"Unknown AI driver '{driver}': choose claude, codex, gemini, or hermes.")

    provider_id = "gemini" if driver_clean == "agy" else driver_clean
    payload = {
        "provider_id": provider_id,
        "cli": driver_clean,
        "account_id": account_id,
        "label": label or account_id,
        "models": model or [],
        "max_parallel": max_parallel,
    }

    async def main() -> None:
        ctx = await _context(local)
        try:
            status, res = await execute_provider_command(ctx.pool, "add_provider", payload, actor="cli")
            if status != "done":
                raise _fail(f"ai add failed: {res.get('error', 'unknown error')}")
            res_status = res.get("status")
            typer.echo(
                f"Added AI provider '{provider_id}' (account: '{account_id}') (status: {res_status})."
            )
            if res.get("next_step"):
                typer.echo(f"Next step: {res['next_step']}")
        finally:
            await ctx.aclose()

    _run_farm(main())


# --- MCP pass-through (OPEN1) -----------------------------------------------------------------------------


@app.command(name="set-secret")
def set_secret_command(
    name: Annotated[str, typer.Argument(help="Environment variable to set, e.g. FARM_MCP_NOTION_TOKEN.")],
    env_file: Annotated[
        Path | None, typer.Option("--env-file", help="The .env to write (default: the repo's .env).")
    ] = None,
) -> None:
    """Save a credential to the local .env. The value is typed hidden, never echoed, printed or logged."""
    from farm.secrets import set_secret

    value = typer.prompt(f"Value for {name}", hide_input=True)
    try:
        path = set_secret(name, value, env_path=env_file)
    except ValueError as exc:
        raise _fail(str(exc)) from None
    typer.echo(f"saved {name} in {path}")


@mcp_app.command(name="import")
def mcp_import(
    source: Annotated[
        str, typer.Option("--from", help="claude-desktop, claude-code, codex or file:<path>")
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would be imported; write nothing.")
    ] = False,
    only: Annotated[
        str | None, typer.Option("--only", help="Comma-separated names of the servers to import.")
    ] = None,
    registry: Annotated[
        Path, typer.Option("--registry", help="The registry file that receives the providers.")
    ] = DEFAULT_REGISTRY,
    env_file: Annotated[
        Path | None, typer.Option("--env-file", help="The .env that receives the secrets.")
    ] = None,
) -> None:
    """Add the MCP servers of Claude Desktop, Claude Code or Codex to the registry; secrets go to .env."""
    from farm.mcp.importer import ImportFailed, format_report, import_servers

    names = [n.strip() for n in only.split(",") if n.strip()] if only else None
    try:
        report = import_servers(
            source, registry_path=registry, env_path=env_file, dry_run=dry_run, only=names
        )
    except ImportFailed as exc:
        raise _fail(str(exc)) from None
    typer.echo(format_report(report))


@mcp_app.command(name="sync")
def mcp_sync(
    provider: Annotated[
        str | None, typer.Argument(help="Only this provider (default: every enabled MCP provider).")
    ] = None,
    local: LocalOption = False,
) -> None:
    """List each MCP server's tools through one of its accounts and store them for `farm serve`."""
    from farm.executors.mcp.client import McpExecutor
    from farm.mcp.sync import sync_all

    async def main() -> bool:
        ctx = await _context(local)
        try:
            executor = ctx.executors["mcp"]
            assert isinstance(executor, McpExecutor)
            results = await sync_all(ctx.pool, executor, only=provider)
        finally:
            await ctx.aclose()
        if not results:
            typer.echo(
                "no MCP provider to sync: add one (`farm mcp import`), then `farm registry sync`", err=True
            )
            return False
        for result in results:
            if not result.ok:
                typer.echo(f"{result.provider}: FAILED  {result.error}")
                continue
            diff = result.diff
            counts = (
                f"+{len(diff.added)} new, {len(diff.changed)} changed, {len(diff.removed)} removed"
                if diff
                else ""
            )
            denied = f", {len(result.denied)} denied" if result.denied else ""
            typer.echo(
                f"{result.provider}: {len(result.tools)} tools via {result.connection} ({counts}{denied})"
            )
        return all(result.ok for result in results)

    if not _run_farm(main()):
        raise typer.Exit(code=1)


@mcp_app.command(name="login")
def mcp_login(
    connection: Annotated[str, typer.Argument(help="Connection id of an OAuth account, e.g. notion-01.")],
    local: LocalOption = False,
) -> None:
    """Log an OAuth account in: the browser opens once and the tokens stay in the account's own store."""
    from farm.executors.mcp.client import McpExecutor
    from farm.executors.mcp.connect import McpFailure
    from farm.mcp import store

    async def main() -> None:
        ctx = await _context(local)
        try:
            executor = ctx.executors["mcp"]
            assert isinstance(executor, McpExecutor)
            async with ctx.pool.connection() as conn:
                cur = await conn.execute(
                    "select provider_id from public.connections where id = %s", (connection,)
                )
                row = await cur.fetchone()
            if row is None:
                raise _fail(f"there is no connection '{connection}' (is the registry synced?)")
            accounts = await store.list_accounts(ctx.pool, row[0])
            view = next(account.view for account in accounts if account.view.id == connection)
            try:
                tools = await executor.login(view)
            except McpFailure as failure:
                raise _fail(f"login failed ({failure.kind.value}): {failure.message}") from None
            async with ctx.pool.connection() as conn:
                await conn.execute(
                    "update public.connections set status = 'active' "
                    "where id = %s and status = 'needs_login'",
                    (connection,),
                )
            typer.echo(f"{connection} is logged in and active ({tools} tools visible); next: farm mcp sync")
        finally:
            await ctx.aclose()

    _run_farm(main())


@mcp_app.command(name="add")
def mcp_add(
    name: Annotated[str, typer.Argument(help="Provider name / ID.")],
    command: Annotated[
        str | None, typer.Option("--command", "-c", help="Command to run for stdio MCP server.")
    ] = None,
    arg: Annotated[
        list[str] | None, typer.Option("--arg", "-a", help="Arguments to pass to the stdio command.")
    ] = None,
    url: Annotated[
        str | None, typer.Option("--url", "-u", help="HTTP / SSE URL for remote MCP server.")
    ] = None,
    env: Annotated[
        list[str] | None, typer.Option("--env", "-e", help="Environment variable names to pass.")
    ] = None,
    header: Annotated[
        list[str] | None, typer.Option("--header", "-H", help="Headers in NAME=ENV format.")
    ] = None,
    auth: Annotated[str, typer.Option("--auth", help="Auth scheme: none, env, oauth.")] = "none",
    namespace: Annotated[
        str | None, typer.Option("--namespace", help="Namespace prefix for exposed tools.")
    ] = None,
    expose: Annotated[str, typer.Option("--expose", help="Exposure mode: direct, discovery, auto.")] = "auto",
    cwd: Annotated[str | None, typer.Option("--cwd", help="Working directory for stdio server.")] = None,
    local: LocalOption = False,
) -> None:
    """Add a new MCP server (stdio or HTTP/SSE)."""
    from farm.control.commands import execute_provider_command

    if not command and not url:
        raise _fail("Specify either --command for stdio or --url for HTTP/SSE MCP server.")
    if command and url:
        raise _fail("Specify either --command or --url, not both.")

    env_dict = {}
    if env:
        for e in env:
            if "=" in e:
                k, v = e.split("=", 1)
                env_dict[k] = v if v.startswith("env:") else f"env:{v}"
            else:
                env_dict[e] = f"env:{e}"

    headers_dict = {}
    if header:
        for h in header:
            if "=" in h:
                k, v = h.split("=", 1)
                headers_dict[k] = v if v.startswith("env:") else f"env:{v}"
            else:
                headers_dict[h] = f"env:{h}"

    payload = {
        "provider_id": name,
        "name": name,
        "kind": "tool",
        "executor": "mcp",
        "command": command,
        "args": arg or [],
        "cwd": cwd,
        "env": env_dict,
        "url": url,
        "headers": headers_dict,
        "auth": auth,
        "namespace": namespace,
        "exposure": expose,
    }

    async def main() -> None:
        ctx = await _context(local)
        try:
            status, res = await execute_provider_command(ctx.pool, "add_provider", payload, actor="cli")
            if status != "done":
                raise _fail(f"mcp add failed: {res.get('error', 'unknown error')}")
            typer.echo(f"Added MCP provider '{name}' (status: {res.get('status')}).")
            if res.get("tools_count", 0) > 0:
                typer.echo(f"Synced {res['tools_count']} tools.")
            if res.get("next_step"):
                typer.echo(f"Next step: {res['next_step']}")
        finally:
            await ctx.aclose()

    _run_farm(main())


@provider_app.command(name="remove")
def provider_remove(
    id: Annotated[str, typer.Argument(help="Provider ID to remove.")],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Force removal even with open jobs or reservations.")
    ] = False,
    local: LocalOption = False,
) -> None:
    """Remove a provider and its connections."""
    from farm.control.commands import execute_provider_command

    payload = {
        "provider_id": id,
        "force": force,
    }

    async def main() -> None:
        ctx = await _context(local)
        try:
            status, res = await execute_provider_command(ctx.pool, "remove_provider", payload, actor="cli")
            if status != "done":
                raise _fail(f"provider remove failed: {res.get('error', 'unknown error')}")
            typer.echo(f"Removed provider '{id}'.")
        finally:
            await ctx.aclose()

    _run_farm(main())


# --- RUN1: farm run, farm connect, farm token, farm alert send --------------------------------------------


@app.command(name="run")
def run_command(
    host: Annotated[
        str | None,
        typer.Option("--host", help="HTTP host to bind (default: settings.http_host)."),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="HTTP port to bind (default: settings.http_port)."),
    ] = None,
    local: LocalOption = False,
) -> None:
    """Run the composed 24/7 Harness Farm: HTTP MCP gateway, command consumer, and background workers."""
    from farm.control.run import run_farm

    _run_farm(run_farm(host=host, port=port, local=local))


@token_app.command(name="create")
def token_create(
    client_name: Annotated[
        str,
        typer.Argument(help="Name of the client or IDE session (e.g. claude-1, cursor)."),
    ],
    local: LocalOption = False,
) -> None:
    """Create a new client token for HTTP MCP authentication."""
    from farm.control.run import create_token

    async def main() -> str:
        ctx = await _context(local)
        try:
            return await create_token(ctx.pool, client_name)
        finally:
            await ctx.aclose()

    tok = _run_farm(main())
    typer.echo(f"Created token for '{client_name}':")
    typer.echo(f"  {tok}")
    typer.echo("Store this token in your environment (e.g. as FARM_TOKEN). It will not be shown again.")


@token_app.command(name="list")
def token_list(local: LocalOption = False) -> None:
    """List all registered client tokens."""
    from farm.control.run import list_tokens

    async def main() -> list[dict[str, Any]]:
        ctx = await _context(local)
        try:
            return await list_tokens(ctx.pool)
        finally:
            await ctx.aclose()

    tokens = _run_farm(main())
    if not tokens:
        typer.echo("no client tokens: create one with 'farm token create <name>'")
        return

    rows: list[list[str]] = [["CLIENT", "HASH (SHA256)", "CREATED", "LAST USED"]]
    for t in tokens:
        h = str(t["token_hash"])
        masked_hash = f"{h[:8]}...{h[-8:]}"
        created = _stamp(t["created_at"])
        last_used = _stamp(t["last_used"]) if t["last_used"] else "never"
        rows.append([str(t["client_name"]), masked_hash, created, last_used])

    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    formatted = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in rows
    ]
    typer.echo("\n".join(formatted))


@token_app.command(name="revoke")
def token_revoke(
    identifier: Annotated[str, typer.Argument(help="Client name or token hash to revoke.")],
    local: LocalOption = False,
) -> None:
    """Revoke a client token."""
    from farm.control.run import revoke_token

    async def main() -> bool:
        ctx = await _context(local)
        try:
            return await revoke_token(ctx.pool, identifier)
        finally:
            await ctx.aclose()

    revoked = _run_farm(main())
    if revoked:
        typer.echo(f"revoked token for '{identifier}'")
    else:
        typer.echo(f"no token found matching '{identifier}'", err=True)
        raise typer.Exit(code=1)


@alert_app.command(name="send")
def alert_send(
    message: Annotated[str, typer.Option("--message", "-m", help="Alert message text.")] = "",
    kind: Annotated[
        str,
        typer.Option("--kind", "-k", help="Alert kind."),
    ] = "farm_down",
    severity: Annotated[
        str,
        typer.Option("--severity", "-s", help="Severity level: info, warn, critical."),
    ] = "warn",
    ref: Annotated[str | None, typer.Option("--ref", "-r", help="Deduplication reference key.")] = None,
    local: LocalOption = False,
) -> None:
    """Send an alert to the database and Telegram."""
    from farm.control.alerts import send_alert

    if not message.strip():
        raise _fail("alert message cannot be empty", code=2)

    async def main() -> str:
        ctx = await _context(local)
        try:
            aid = await send_alert(
                ctx.pool,
                kind=kind,
                message=message,
                severity=severity,
                ref=ref,
            )
            return str(aid)
        finally:
            await ctx.aclose()

    alert_id = _run_farm(main())
    typer.echo(f"alert sent: {alert_id}")


@app.command(name="connect")
def connect_command(
    target: Annotated[
        str,
        typer.Argument(help="Target IDE: claude-code, codex, cursor, gemini, antigravity, generic."),
    ],
    write: Annotated[
        bool,
        typer.Option("--write", help="Write configuration to the target IDE config file."),
    ] = False,
    skill: Annotated[
        bool,
        typer.Option("--skill", help="Print or write the skill file / snippet for the target IDE."),
    ] = False,
    host: Annotated[str | None, typer.Option(help="Farm HTTP host.")] = None,
    port: Annotated[int | None, typer.Option(help="Farm HTTP port.")] = None,
    env_var: Annotated[str, typer.Option(help="Environment variable holding the token.")] = "FARM_TOKEN",
) -> None:
    """Print or write the snippet to attach an IDE or agent to the running Farm."""
    from farm.settings import http_host as get_http_host
    from farm.settings import http_path as get_http_path
    from farm.settings import http_port as get_http_port

    target_clean = target.strip().lower().replace("_", "-")
    h = host or get_http_host()
    p = port or get_http_port()
    path = get_http_path()
    url = f"http://{h}:{p}{path}"

    home = Path.home()
    env = os.environ

    if skill:
        root = Path(__file__).resolve().parent.parent.parent
        skill_file = root / "docs" / "skills" / "harness-farm" / "SKILL.md"
        skill_text = (
            skill_file.read_text(encoding="utf-8")
            if skill_file.is_file()
            else "# Harness Farm Skill\n"
        )

        if target_clean in ("claude-code", "claude"):
            skill_dest = home / ".claude" / "skills" / "harness-farm" / "SKILL.md"
            typer.echo("=== Claude Code Skill ===")
            typer.echo("Skill path: ~/.claude/skills/harness-farm/SKILL.md")
            typer.echo(f"Target:     {skill_dest}")
            typer.echo()
            typer.echo("To install manually, copy docs/skills/harness-farm/SKILL.md to the path above.")
            if not write:
                return

            confirmed = typer.confirm(f"\nWrite skill to {skill_dest}?", default=False)
            if not confirmed:
                typer.echo("Cancelled.")
                return

            skill_dest.parent.mkdir(parents=True, exist_ok=True)
            skill_dest.write_text(skill_text, encoding="utf-8")
            typer.echo(f"Wrote skill to {skill_dest}")
            return

        elif target_clean == "codex":
            codex_dir = Path(env.get("CODEX_HOME") or home / ".codex")
            agents_dest = codex_dir / "AGENTS.md"
            codex_snippet = (
                "<!-- Harness Farm Skill -->\n"
                "## Harness Farm Capabilities\n"
                "- Before starting tasks, call `farm_guide()` to inspect available tools and quotas.\n"
                "- Credit etiquette: use read-only inspection tools first; ask before spending.\n"
                "- Missing integration: call `request_integration(name, kind, purpose)` to file request.\n"
            )
            typer.echo("=== Codex Agents Skill Snippet ===")
            typer.echo(f"Target: {agents_dest}")
            typer.echo()
            typer.echo("Snippet for AGENTS.md:")
            typer.echo(codex_snippet)
            if not write:
                return

            confirmed = typer.confirm(f"\nAppend snippet to {agents_dest}?", default=False)
            if not confirmed:
                typer.echo("Cancelled.")
                return

            agents_dest.parent.mkdir(parents=True, exist_ok=True)
            existing_agents_text = (
                agents_dest.read_text(encoding="utf-8") if agents_dest.is_file() else ""
            )
            sep = "\n\n" if existing_agents_text.strip() else ""
            new_text = existing_agents_text.rstrip() + sep + codex_snippet
            agents_dest.write_text(new_text, encoding="utf-8")
            typer.echo(f"Wrote snippet to {agents_dest}")
            return

        else:
            typer.echo(f"=== {target} Skill ===")
            typer.echo("Generic skill file located at: docs/skills/harness-farm/SKILL.md")
            typer.echo("Copy its contents into your agent instructions or custom skill directory.")
            return

    if target_clean in ("claude-code", "claude"):
        config_path = home / ".claude.json"
        is_verified = True
        typer.echo("=== Claude Code Connection [VERIFIED] ===")
        typer.echo(f"Endpoint: {url}")
        typer.echo(f"Token env var: {env_var}")
        typer.echo()
        typer.echo("Step 1: Set the token in your environment:")
        typer.echo(f"  $env:{env_var}=\"<your-farm-token>\"  # PowerShell")
        typer.echo(f"  export {env_var}=\"<your-farm-token>\"  # Bash / Zsh")
        typer.echo()
        typer.echo("Step 2: Add via Claude Code CLI:")
        cli_cmd = (
            f"  claude mcp add --transport http harness-farm {url} "
            f"--header \"Authorization: Bearer ${{{env_var}}}\""
        )
        typer.echo(cli_cmd)
        typer.echo()
        typer.echo(f"Or add to {config_path}:")
        snippet = {
            "mcpServers": {
                "harness-farm": {
                    "type": "streamable-http",
                    "url": url,
                    "headers": {
                        "Authorization": f"Bearer ${{{env_var}}}"
                    },
                }
            }
        }
        typer.echo(json.dumps(snippet, indent=2))

    elif target_clean == "codex":
        config_dir = Path(env.get("CODEX_HOME") or home / ".codex")
        config_path = config_dir / "config.toml"
        is_verified = True
        typer.echo("=== Codex Connection [VERIFIED] ===")
        typer.echo(f"Endpoint: {url}")
        typer.echo(f"Token env var: {env_var}")
        typer.echo()
        typer.echo("Step 1: Set the token in your environment:")
        typer.echo(f"  $env:{env_var}=\"<your-farm-token>\"  # PowerShell")
        typer.echo(f"  export {env_var}=\"<your-farm-token>\"  # Bash / Zsh")
        typer.echo()
        typer.echo(f"Step 2: Add to {config_path}:")
        toml_snippet = (
            f"[mcp_servers.harness-farm]\n"
            f"url = \"{url}\"\n"
            f"bearer_token_env_var = \"{env_var}\"\n"
        )
        typer.echo(toml_snippet)

    elif target_clean == "cursor":
        config_path = home / ".cursor" / "mcp.json"
        is_verified = False
        typer.echo("=== Cursor Connection [UNVERIFIED] ===")
        typer.echo(f"Endpoint: {url}")
        typer.echo("Note: Cursor MCP configuration format is unverified for header env-var expansion.")
        typer.echo()
        typer.echo(f"Add to {config_path}:")
        snippet = {
            "mcpServers": {
                "harness-farm": {
                    "url": url,
                    "headers": {
                        "Authorization": f"Bearer ${{{env_var}}}"
                    },
                }
            }
        }
        typer.echo(json.dumps(snippet, indent=2))

    elif target_clean in ("gemini", "antigravity", "agy"):
        config_path = home / ".gemini" / "config" / "mcp_config.json"
        is_verified = False
        typer.echo("=== Antigravity / Gemini CLI Connection [UNVERIFIED] ===")
        typer.echo(f"Endpoint: {url}")
        typer.echo("Note: Antigravity remote HTTP MCP headers/env-var support is unverified.")
        typer.echo()
        typer.echo(f"Add to {config_path}:")
        snippet = {
            "mcpServers": {
                "harness-farm": {
                    "url": url,
                    "headers": {
                        "Authorization": f"Bearer ${{{env_var}}}"
                    },
                }
            }
        }
        typer.echo(json.dumps(snippet, indent=2))

    elif target_clean == "generic":
        config_path = Path("mcp.json")
        is_verified = False
        typer.echo("=== Generic MCP Client Connection [UNVERIFIED] ===")
        typer.echo(f"Endpoint URL: {url}")
        typer.echo("Transport: streamable-http (or http)")
        typer.echo(f"Authorization: Bearer ${{{env_var}}}")

    else:
        raise _fail(
            f"unknown target '{target}': choose from claude-code, codex, cursor, "
            "gemini, antigravity, generic",
            code=2,
        )

    if write:
        if not is_verified:
            typer.echo(
                f"\nCannot write: format and env-var support for '{target}' is unverified.",
                err=True,
            )
            raise typer.Exit(code=1)

        confirmed = typer.confirm(f"\nWrite configuration to {config_path}?", default=False)
        if not confirmed:
            typer.echo("Cancelled.")
            return

        config_path.parent.mkdir(parents=True, exist_ok=True)
        if target_clean in ("claude-code", "claude"):
            existing: dict[str, Any] = {}
            if config_path.is_file():
                try:
                    existing = json.loads(config_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            mcp_servers = existing.setdefault("mcpServers", {})
            mcp_servers["harness-farm"] = {
                "type": "streamable-http",
                "url": url,
                "headers": {
                    "Authorization": f"Bearer ${{{env_var}}}"
                },
            }
            config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            typer.echo(f"Wrote configuration to {config_path}")

        elif target_clean == "codex":
            existing_text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
            if "[mcp_servers.harness-farm]" in existing_text:
                typer.echo(f"Configuration already contains [mcp_servers.harness-farm] in {config_path}")
            else:
                new_text = existing_text.rstrip() + ("\n\n" if existing_text.strip() else "") + toml_snippet
                config_path.write_text(new_text, encoding="utf-8")
                typer.echo(f"Wrote configuration to {config_path}")


@app.command(name="guide")
def guide_command(
    section: Annotated[
        str,
        typer.Option("--section", "-s", help="Section: all, mcp, ai, rules, recipes, need."),
    ] = "all",
    local: LocalOption = False,
) -> None:
    """Print the live capability guide for humans."""
    from farm.gateway.guide import generate_guide

    async def main() -> str:
        ctx = await _context(local)
        try:
            return await generate_guide(ctx.pool, ctx.clock(), section=section)  # type: ignore[arg-type]
        finally:
            await ctx.aclose()

    text = _run_farm(main())
    typer.echo(text)


def _format_requests_table(reqs: list[Any]) -> str:
    rows = [["ID", "NAME", "KIND", "STATUS", "URGENCY", "REQUESTED_BY", "PURPOSE"]]
    for r in reqs:
        rows.append([
            str(r.id)[:8],
            r.name,
            r.kind,
            r.status,
            r.urgency,
            r.requested_by,
            (r.purpose[:40] + "...") if len(r.purpose) > 40 else r.purpose,
        ])
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows)


@requests_app.command(name="list")
def requests_list(
    status: Annotated[
        str | None,
        typer.Option("--status", "-s", help="Filter by status: open, in_progress, done, declined."),
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=500, help="Max requests to list.")] = 50,
    local: LocalOption = False,
) -> None:
    """List integration requests."""
    from farm.control.integration_requests import list_integration_requests

    async def main() -> list[Any]:
        ctx = await _context(local)
        try:
            return await list_integration_requests(ctx.pool, status=status, limit=limit)
        finally:
            await ctx.aclose()

    reqs = _run_farm(main())
    if not reqs:
        typer.echo("No integration requests found.")
        return
    typer.echo(_format_requests_table(reqs))


@requests_app.command(name="show")
def requests_show(
    request_id: Annotated[str, typer.Argument(help="Integration request ID (UUID or prefix).")],
    local: LocalOption = False,
) -> None:
    """Show details of an integration request."""
    from uuid import UUID

    from farm.control.integration_requests import get_integration_request, list_integration_requests

    async def main() -> Any:
        ctx = await _context(local)
        try:
            try:
                uid = UUID(request_id)
                return await get_integration_request(ctx.pool, uid)
            except ValueError:
                all_reqs = await list_integration_requests(ctx.pool, limit=500)
                matches = [r for r in all_reqs if str(r.id).startswith(request_id)]
                if len(matches) == 1:
                    return matches[0]
                if len(matches) > 1:
                    raise _fail(
                        f"Ambiguous request ID '{request_id}' matches {len(matches)} requests",
                        code=2,
                    ) from None
                return None
        finally:
            await ctx.aclose()

    req = _run_farm(main())
    if req is None:
        raise _fail(f"Integration request '{request_id}' not found", code=1)

    typer.echo(f"ID:           {req.id}")
    typer.echo(f"Name:         {req.name}")
    typer.echo(f"Kind:         {req.kind}")
    typer.echo(f"Status:       {req.status}")
    typer.echo(f"Urgency:      {req.urgency}")
    typer.echo(f"Requested By: {req.requested_by}")
    typer.echo(f"Created At:   {_stamp(req.created_at)}")
    typer.echo(f"Purpose:      {req.purpose}")
    if req.context:
        typer.echo(f"Context:      {req.context}")
    if req.links:
        typer.echo(f"Links:        {', '.join(req.links)}")
    if req.owner_note:
        typer.echo(f"Owner Note:   {req.owner_note}")
    if req.resolved_at:
        typer.echo(f"Resolved At:  {_stamp(req.resolved_at)}")


@requests_app.command(name="resolve")
def requests_resolve(
    request_id: Annotated[str, typer.Argument(help="Integration request ID (UUID or prefix).")],
    status: Annotated[
        str,
        typer.Option("--status", "-s", help="Resolution status: in_progress, done, declined."),
    ] = "done",
    note: Annotated[str | None, typer.Option("--note", "-n", help="Owner note.")] = None,
    local: LocalOption = False,
) -> None:
    """Resolve an integration request with a status and note."""
    from uuid import UUID

    from farm.control.integration_requests import list_integration_requests, resolve_integration_request

    async def main() -> Any:
        ctx = await _context(local)
        try:
            target_id = request_id
            try:
                UUID(request_id)
            except ValueError:
                all_reqs = await list_integration_requests(ctx.pool, limit=500)
                matches = [r for r in all_reqs if str(r.id).startswith(request_id)]
                if len(matches) == 1:
                    target_id = str(matches[0].id)
                elif len(matches) > 1:
                    raise _fail(
                        f"Ambiguous request ID '{request_id}' matches {len(matches)} requests",
                        code=2,
                    ) from None
                else:
                    raise _fail(f"Integration request '{request_id}' not found", code=1) from None

            return await resolve_integration_request(ctx.pool, target_id, status=status, owner_note=note)
        finally:
            await ctx.aclose()

    req = _run_farm(main())
    typer.echo(f"Integration request {req.id} resolved: {req.status}")



