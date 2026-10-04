"""Schema contract (CONTEXT section 3): tables, key columns, checks, RLS, triggers, cascades, migrations."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from psycopg import errors

from farm.control.cli import alembic_config
from farm.db.pool import DbPool
from tests.conftest import seed_connection

KEY_COLUMNS: dict[str, set[str]] = {
    "farm_settings": {"owner_email", "global_monthly_budget_usd", "alert_thresholds", "timezone"},
    "providers": {"id", "name", "kind", "executor", "default_strategy", "enabled", "config"},
    "connections": {
        "id",
        "provider_id",
        "label",
        "auth_ref",
        "scope",
        "priority",
        "strategy",
        "concurrency",
        "rate_per_min",
        "status",
        "plan",
        "meta",
    },
    "consumption_units": {
        "connection_id",
        "unit",
        "limit_value",
        "period",
        "reset_anchor",
        "next_reset_at",
        "charged_on",
        "unit_cost_usd",
        "estimate_per_call",
    },
    "quota_usage": {"connection_id", "unit", "period_start", "used", "reserved", "limit_value"},
    "quota_reservations": {
        "id",
        "connection_id",
        "unit",
        "amount",
        "request_id",
        "period_start",
        "status",
        "expires_at",
        "actual",
    },
    "usage_events": {
        "id",
        "connection_id",
        "unit",
        "amount",
        "kind",
        "cost_usd",
        "request_id",
        "run_id",
        "at",
    },
    "balance_snapshots": {"id", "connection_id", "unit", "remaining", "source", "at"},
    "connection_health": {
        "connection_id",
        "circuit",
        "consecutive_failures",
        "last_error_kind",
        "last_error",
        "last_error_at",
        "cooldown_until",
        "success_count",
        "failure_count",
        "last_success_at",
        "latency_ms_p50",
    },
    "budgets": {"id", "scope", "ref", "monthly_usd", "hard_stop"},
    "billing_events": {"id", "connection_id", "kind", "amount_usd", "at", "note"},
    "alerts": {"id", "kind", "severity", "message", "ref", "created_at", "acked_at"},
    "capabilities": {
        "name",
        "kind",
        "description",
        "input_schema",
        "output_schema",
        "default_strategy",
        "cache_ttl_seconds",
    },
    "capability_routes": {"capability", "provider_id", "position", "enabled"},
    "capability_requests": {
        "id",
        "request_hash",
        "capability",
        "params",
        "status",
        "result",
        "run_id",
        "created_at",
        "expires_at",
    },
    "runs": {
        "id",
        "capability",
        "request_id",
        "caller",
        "strategy",
        "status",
        "cost_usd",
        "cached",
        "connection_id",
        "error_kind",
        "error",
        "started_at",
        "finished_at",
    },
    "run_events": {"id", "run_id", "seq", "kind", "connection_id", "data", "at"},
    "entities": {"id", "kind", "canonical_key", "name"},
    "facts": {
        "id",
        "entity_id",
        "attribute",
        "value",
        "source_connection_id",
        "observed_at",
        "expires_at",
        "confidence",
        "evidence_ids",
    },
    "evidence": {"id", "sha256", "path", "url", "thumb_path", "captured_at", "tool_version"},
    "ai_sessions": {"session_id", "connection_id", "ai", "model", "created_at", "last_used_at"},
    "farm_commands": {"id", "kind", "payload", "status", "result", "created_by", "created_at", "done_at"},
    "audit_events": {"id", "actor", "action", "target", "before", "after", "at"},
}
FAKE_PW = "hunter2-not-a-real-password"  # sentinel that must never appear in CLI output
DEFAULT_WORKSPACE = "00000000-0000-0000-0000-000000000001"
FUNCTIONS = {
    "farm_period_start",
    "farm_reserve",
    "farm_commit",
    "farm_release",
    "farm_expire_reservations",
}


def _rows(url: str, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(url, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


def test_every_table_exists_with_key_columns(db_url: str) -> None:
    rows = _rows(
        db_url,
        "select table_name, column_name from information_schema.columns where table_schema = 'public'",
    )
    columns: dict[str, set[str]] = {}
    for table, column in rows:
        columns.setdefault(table, set()).add(column)
    for table, expected in KEY_COLUMNS.items():
        assert table in columns, f"missing table {table}"
        assert expected <= columns[table], f"{table}: missing {expected - columns[table]}"


def test_every_table_has_workspace_id_default(db_url: str) -> None:
    rows = _rows(
        db_url,
        "select table_name, data_type, is_nullable, column_default from information_schema.columns "
        "where table_schema = 'public' and column_name = 'workspace_id'",
    )
    assert {r[0] for r in rows} == set(KEY_COLUMNS)
    for table, data_type, nullable, default in rows:
        assert data_type == "uuid" and nullable == "NO", table
        assert DEFAULT_WORKSPACE in default, table


def test_ledger_functions_exist(db_url: str) -> None:
    rows = _rows(
        db_url,
        "select proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public'",
    )
    assert FUNCTIONS <= {r[0] for r in rows}


def test_rls_enabled_on_all_tables(db_url: str) -> None:
    rows = _rows(db_url, "select tablename, rowsecurity from pg_tables where schemaname = 'public'")
    tables = {r[0]: r[1] for r in rows}
    assert set(KEY_COLUMNS) <= set(tables)
    assert [t for t, enabled in tables.items() if not enabled] == []


def test_farm_settings_singleton_defaults(db_url: str) -> None:
    rows = _rows(db_url, "select id, owner_email, alert_thresholds, timezone from public.farm_settings")
    assert rows == [(1, None, [50, 80, 100], "UTC")]
    with psycopg.connect(db_url, autocommit=True) as conn, pytest.raises(errors.CheckViolation):
        conn.execute("insert into public.farm_settings (id) values (2)")


BAD_ROWS = [
    "insert into providers (id, name, kind, executor) values ('x', 'x', 'robot', 'api')",
    "insert into providers (id, name, kind, executor) values ('x', 'x', 'tool', 'ftp')",
    "insert into connections (id, provider_id, status) values ('c2', 'p', 'sleeping')",
    "insert into consumption_units (connection_id, unit, period) values ('c', 'u2', 'fortnight')",
    "insert into consumption_units (connection_id, unit, period, charged_on) values ('c', 'u2', 'day', 'whim')",
    "insert into quota_usage (connection_id, unit, period_start, used) values ('c', 'u', now(), -1)",
    "insert into quota_usage (connection_id, unit, period_start, reserved) values ('c', 'u', now(), -1)",
    "insert into budgets (scope, monthly_usd) values ('galaxy', 1)",
    "insert into billing_events (connection_id, kind, amount_usd) values ('c', 'gift', 1)",
    "insert into balance_snapshots (connection_id, unit, remaining, source) values ('c', 'u', 1, 'guess')",
    "insert into usage_events (connection_id, unit, amount, kind) values ('c', 'u', 1, 'maybe')",
    "insert into connection_health (connection_id, circuit) values ('c', 'ajar')",
    "insert into alerts (kind, severity, message) values ('k', 'loud', 'm')",
    "insert into capabilities (name, kind) values ('cap', 'magic')",
    "insert into farm_commands (kind) values ('explode')",
    "insert into farm_commands (kind, status) values ('pause', 'maybe')",
]


@pytest.mark.parametrize("statement", BAD_ROWS)
async def test_check_constraints_reject_bad_values(pool: DbPool, statement: str) -> None:
    await seed_connection(pool, "p", "c", {"u": 1})
    async with pool.connection() as conn:
        with pytest.raises(errors.CheckViolation):
            await conn.execute(statement)


async def test_run_events_and_requests_checks(pool: DbPool) -> None:
    async with pool.connection() as conn:
        run_id = await (
            await conn.execute(
                "insert into runs (capability, caller, status) values ('cap', 'test', 'running') returning id"
            )
        ).fetchone()
        assert run_id is not None
        with pytest.raises(errors.CheckViolation):
            await conn.execute(
                "insert into run_events (run_id, seq, kind) values (%s, 1, 'teleport')", (run_id[0],)
            )
        await conn.execute("insert into run_events (run_id, seq, kind) values (%s, 1, 'plan')", (run_id[0],))
        with pytest.raises(errors.CheckViolation):
            await conn.execute("insert into runs (capability, caller, status) values ('cap', 'test', 'lost')")
        with pytest.raises(errors.CheckViolation):
            await conn.execute(
                "insert into capability_requests (request_hash, capability, status) values ('h', 'cap', 'weird')"
            )


async def test_capability_request_hash_is_unique_per_workspace(pool: DbPool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "insert into capability_requests (request_hash, capability, status) values ('h1', 'cap', 'pending')"
        )
        with pytest.raises(errors.UniqueViolation):
            await conn.execute(
                "insert into capability_requests (request_hash, capability, status) values ('h1', 'cap', 'pending')"
            )


async def test_updated_at_trigger(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c")
    async with pool.connection() as conn:
        before = await (await conn.execute("select updated_at from connections where id = 'c'")).fetchone()
        await conn.execute("update connections set priority = 5 where id = 'c'")
        after = await (await conn.execute("select updated_at from connections where id = 'c'")).fetchone()
    assert before is not None and after is not None and after[0] > before[0]


async def test_connection_defaults(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c")
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "select scope, priority, strategy, concurrency, rate_per_min, status, plan, meta "
                "from connections where id = 'c'"
            )
        ).fetchone()
    assert row == (["internal"], 100, None, 1, None, "active", {}, {})


async def test_deleting_a_connection_cascades_to_children(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    async with pool.connection() as conn:
        await conn.execute("select public.farm_reserve('c', 'credits', 1)")
        await conn.execute("insert into connection_health (connection_id) values ('c')")
        await conn.execute(
            "insert into usage_events (connection_id, unit, amount, kind) values ('c', 'credits', 1, 'actual')"
        )
        await conn.execute(
            "insert into balance_snapshots (connection_id, unit, remaining, source) values ('c', 'credits', 9, 'api')"
        )
        await conn.execute(
            "insert into billing_events (connection_id, kind, amount_usd) values ('c', 'charge', 5)"
        )
        await conn.execute(
            "insert into ai_sessions (session_id, connection_id, ai) values ('s', 'c', 'claude')"
        )
        await conn.execute("delete from connections where id = 'c'")
        for table in (
            "consumption_units",
            "quota_usage",
            "quota_reservations",
            "connection_health",
            "usage_events",
            "balance_snapshots",
            "billing_events",
            "ai_sessions",
        ):
            count = await (await conn.execute(f"select count(*) from public.{table}")).fetchone()
            assert count == (0,), table


async def test_deleting_a_provider_cascades_to_connections_and_routes(pool: DbPool) -> None:
    await seed_connection(pool, "p", "c", {"credits": 10})
    async with pool.connection() as conn:
        await conn.execute("insert into capabilities (name, kind) values ('cap', 'tool')")
        await conn.execute(
            "insert into capability_routes (capability, provider_id, position) values ('cap', 'p', 0)"
        )
        await conn.execute("delete from providers where id = 'p'")
        assert await (await conn.execute("select count(*) from connections")).fetchone() == (0,)
        assert await (await conn.execute("select count(*) from capability_routes")).fetchone() == (0,)
        assert await (await conn.execute("select count(*) from capabilities")).fetchone() == (1,)


def test_migration_round_trip(scratch_db: Callable[[], str]) -> None:
    """downgrade base removes everything, upgrade head recreates it (and migrations are re-runnable)."""
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # no-op
    tables_at_head = sorted(
        r[0] for r in _rows(url, "select tablename from pg_tables where schemaname = 'public'")
    )
    assert set(KEY_COLUMNS) <= set(tables_at_head)

    command.downgrade(cfg, "base")
    left = {r[0] for r in _rows(url, "select tablename from pg_tables where schemaname = 'public'")}
    assert left <= {"alembic_version"}
    funcs = _rows(
        url,
        "select proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public'",
    )
    assert funcs == []

    command.upgrade(cfg, "head")
    again = sorted(r[0] for r in _rows(url, "select tablename from pg_tables where schemaname = 'public'"))
    assert again == tables_at_head


# --- Supabase-style RLS: emulate the pieces of the `auth` schema the policies rely on ---------------------

FAKE_AUTH = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;
create schema auth;
create function auth.jwt() returns jsonb language sql stable
  as $$ select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;
grant usage on schema auth to authenticated, anon;
grant execute on function auth.jwt() to authenticated, anon;
grant usage on schema public to anon;
"""


def _as(conn: psycopg.Connection[Any], role: str, email: str | None) -> None:
    conn.execute(f"set role {role}")  # role names come from this file only
    claims = {"role": role, **({"email": email} if email else {})}
    conn.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps(claims),))


def test_rls_policies_when_supabase_auth_schema_exists(scratch_db: Callable[[], str]) -> None:
    url = scratch_db()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(FAKE_AUTH)
    command.upgrade(alembic_config(url), "head")

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("update public.farm_settings set owner_email = 'Owner@Example.com' where id = 1")
        conn.execute(
            "insert into public.providers (id, name, kind, executor) values ('p', 'P', 'tool', 'api')"
        )

        # owner (case-insensitive email): reads every table, inserts queued commands only
        _as(conn, "authenticated", "owner@example.com")
        assert conn.execute("select count(*) from public.providers").fetchone() == (1,)
        assert conn.execute("select owner_email from public.farm_settings").fetchone() == (
            "Owner@Example.com",
        )
        conn.execute("insert into public.farm_commands (kind, created_by) values ('pause', 'console')")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("insert into public.farm_commands (kind, status) values ('pause', 'done')")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "insert into public.providers (id, name, kind, executor) values ('q', 'Q', 'tool', 'api')"
            )
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("update public.providers set name = 'X'")
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("delete from public.farm_commands")
        # quota functions are not callable through the Data API roles
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("select public.farm_expire_reservations()")
        conn.execute("reset role")

        # another signed-in user, a user without email and the anon role see nothing and write nothing
        for role, email in (("authenticated", "intruder@example.com"), ("authenticated", None)):
            _as(conn, role, email)
            assert conn.execute("select count(*) from public.providers").fetchone() == (0,)
            assert conn.execute("select count(*) from public.farm_settings").fetchone() == (0,)
            assert conn.execute("select count(*) from public.farm_commands").fetchone() == (0,)
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute("insert into public.farm_commands (kind) values ('pause')")
            conn.execute("reset role")
        _as(conn, "anon", None)
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("select count(*) from public.providers")
        conn.execute("reset role")

        # the owner (superuser here, postgres on Supabase) is not subject to the policies
        assert conn.execute("select count(*) from public.farm_commands").fetchone() == (1,)

    # a null owner_email matches nobody, not even a token without email
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("update public.farm_settings set owner_email = null where id = 1")
        _as(conn, "authenticated", None)
        assert conn.execute("select count(*) from public.providers").fetchone() == (0,)
        conn.execute("reset role")

    # downgrade removes the policies and helper cleanly
    command.downgrade(alembic_config(url), "base")
    assert _rows(url, "select count(*) from pg_policies where schemaname = 'public'") == [(0,)]


# --- URL resolution, CLI guards and local-server helpers (no database needed) -------------------------------


def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("farm.db.pool.load_env", lambda: None)
    for name in ("FARM_DB_URL", "SUPABASE_DB_URL"):
        monkeypatch.delenv(name, raising=False)


def test_get_db_url_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    from farm.db.pool import get_db_url

    _no_dotenv(monkeypatch)
    monkeypatch.setattr("farm.db.local.start_local", lambda: "postgresql://postgres:@127.0.0.1:1/postgres")
    assert get_db_url() == "postgresql://postgres:@127.0.0.1:1/postgres"  # neither set -> local mode
    monkeypatch.setenv("SUPABASE_DB_URL", " postgresql://u:p@db.example.com/postgres ")
    assert get_db_url() == "postgresql://u:p@db.example.com/postgres"
    monkeypatch.setenv("FARM_DB_URL", "postgresql://farm:pw@10.0.0.5/farm")
    assert get_db_url() == "postgresql://farm:pw@10.0.0.5/farm"  # FARM_DB_URL wins
    assert get_db_url(force_local=True) == "postgresql://postgres:@127.0.0.1:1/postgres"


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("postgresql://postgres:@127.0.0.1:5432/postgres", True),
        ("postgresql://postgres@localhost/postgres", True),
        ("postgresql://postgres@[::1]:5432/postgres", True),
        ("host=127.0.0.1 port=5432 dbname=x user=postgres", True),
        ("postgresql://postgres:@/postgres?host=/tmp/pgsock", True),
        ("postgresql://postgres:pw@db.example.supabase.co:5432/postgres", False),
        ("postgresql://u:" + "localhost@db.example.com/postgres", False),  # substring in the password
        ("postgresql://u:pw@db.example.com/postgres?options=-c%20search_path=localhost", False),
        ("postgresql://u:pw@127.0.0.1.example.com/postgres", False),
        ("host=db.example.com dbname=x", False),
        ("not a url at all ===", False),
    ],
)
def test_is_local_url_parses_the_host(url: str, local: bool) -> None:
    from farm.db.pool import is_local_url

    assert is_local_url(url) is local


def test_reset_local_refuses_a_remote_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from farm.control.cli import app

    _no_dotenv(monkeypatch)
    monkeypatch.setenv(
        "SUPABASE_DB_URL", "postgresql://postgres:" + FAKE_PW + "@db.example.supabase.co:5432/postgres"
    )
    called: list[bool] = []
    monkeypatch.setattr("farm.db.local.reset_local", lambda *a, **k: called.append(True) or "")
    result = CliRunner().invoke(app, ["db", "reset-local"])
    assert result.exit_code == 1
    assert "REFUSED" in result.stdout and FAKE_PW not in result.stdout
    assert called == []


def test_cli_errors_never_print_passwords(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from farm.control.cli import app

    _no_dotenv(monkeypatch)
    monkeypatch.setenv("FARM_DB_URL", "postgresql://farm:" + FAKE_PW + "@127.0.0.1:1/nodb?connect_timeout=1")
    result = CliRunner().invoke(app, ["db", "migrate"])
    assert result.exit_code == 1
    assert "db migrate FAIL" in result.output
    assert FAKE_PW not in result.output


@pytest.mark.parametrize(
    "message",
    [
        "connection to postgresql://farm:" + FAKE_PW + "@db.example.com:5432/postgres failed",
        "could not connect: postgres://farm:" + FAKE_PW + "@10.0.0.5/farm?sslmode=require",
        "dsn postgresql+psycopg://u:" + FAKE_PW + "@h/d broke",
    ],
)
def test_safe_masks_credentials_in_error_text(message: str) -> None:
    from farm.control.cli import _safe

    masked = _safe(RuntimeError(message))
    assert FAKE_PW not in masked
    assert masked.startswith("RuntimeError: ") and "***@" in masked


def test_local_conf_block_is_idempotent(tmp_path: Path) -> None:
    from farm.db.local import _apply_local_conf

    conf = tmp_path / "postgresql.conf"
    conf.write_text("max_connections = 100\n#fsync = on\n", encoding="utf-8")
    assert _apply_local_conf(tmp_path) is True
    first = conf.read_text(encoding="utf-8")
    assert first.count("fsync = off") == 1 and first.startswith("max_connections = 100\n")
    assert _apply_local_conf(tmp_path) is False  # unchanged on the second run
    conf.write_text(first.replace("fsync = off", "fsync = on"), encoding="utf-8")  # someone edited the block
    assert _apply_local_conf(tmp_path) is True
    assert conf.read_text(encoding="utf-8") == first


def test_stale_postmaster_pid_is_detected_and_cleared(tmp_path: Path) -> None:
    import os

    from farm.db.local import _clear_stale_pid, _postmaster_alive

    pid_file = tmp_path / "postmaster.pid"
    lines = ["999999", str(tmp_path), "1700000000", "5432", "", "127.0.0.1", "  1  2", "ready"]
    pid_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _postmaster_alive(tmp_path) is False  # no such process
    lines[0] = str(os.getpid())  # a live process that is not postgres (recycled PID)
    pid_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _postmaster_alive(tmp_path) is False
    _clear_stale_pid(tmp_path)
    assert not pid_file.exists()


def _kill_tree(pid: int) -> None:
    import contextlib

    import psutil

    postmaster = psutil.Process(pid)
    victims = [*postmaster.children(recursive=True), postmaster]
    for proc in victims:
        # workers may already be gone: they exit on their own once the postmaster dies
        with contextlib.suppress(psutil.NoSuchProcess):
            proc.kill()
    psutil.wait_procs(victims, timeout=10)


@pytest.fixture
def crash_dir() -> Iterator[Path]:
    """A private pgdata location under FARM_DATA_DIR, removed (server stopped) afterwards."""
    import shutil
    import uuid

    from farm.db.local import _log_path, stop_local
    from farm.settings import data_dir

    pgdata = data_dir() / f"pgcrash_{uuid.uuid4().hex[:8]}"
    yield pgdata
    stop_local(pgdata)
    shutil.rmtree(pgdata, ignore_errors=True)
    for debris in pgdata.parent.glob(f"{pgdata.name}.init-*"):
        shutil.rmtree(debris, ignore_errors=True)
    for logfile in _log_path(pgdata).parent.glob(f"{pgdata.name}.*"):
        logfile.unlink(missing_ok=True)


@pytest.mark.parametrize("durable", [True, False], ids=["fsync-on", "fsync-off"])
def test_local_server_restarts_after_a_hard_kill(crash_dir: Path, durable: bool) -> None:
    """Regression: after the postmaster was killed, the restart timed out on Windows. Crash recovery fsyncs
    every file in the data directory and the new postmaster held the server log, which then lived inside it,
    open without write sharing: ``could not open file "./log": sharing violation``, retried for 30 s while
    pgserver gives up after 10 s. Must work with ``fsync = on`` (the owner's runtime database) as well as
    ``off`` (throwaway test databases). Also covers what a crash or PC shutdown leaves around: a stale, torn
    postmaster.pid and a corrupt pgserver handle list."""
    from farm.db.local import _log_path, get_local_server

    server = get_local_server(crash_dir, durable=durable)
    with psycopg.connect(server.get_uri(), autocommit=True) as conn:
        assert conn.execute("show fsync").fetchone() == ("on" if durable else "off",)
        conn.execute("create table survivor (n int)")
        conn.execute("insert into survivor values (42)")
    postmaster_pid = server.get_postmaster_info().pid
    _kill_tree(postmaster_pid)

    pid_file = crash_dir / "postmaster.pid"
    assert pid_file.exists()  # what a hard kill leaves behind
    pid_file.write_text("\n".join(pid_file.read_text(encoding="utf-8").splitlines()[:3]), encoding="utf-8")
    (crash_dir / ".handle_pids.json").write_text("", encoding="utf-8")  # torn write

    restarted = get_local_server(crash_dir, durable=durable)  # must recover instead of raising
    assert restarted.get_postmaster_info().pid != postmaster_pid
    with psycopg.connect(restarted.get_uri(), autocommit=True) as conn:
        assert conn.execute("show fsync").fetchone() == ("on" if durable else "off",)
        assert conn.execute("select n from survivor").fetchall() == [(42,)]

    server_log = _log_path(crash_dir).read_text(encoding="utf-8", errors="replace")
    assert "automatic recovery in progress" in server_log  # the restart really went through crash recovery
    assert "sharing violation" not in server_log
    assert not (crash_dir / "log").exists()  # the log is not inside the data directory


def test_legacy_log_inside_the_data_directory_is_moved_out(crash_dir: Path) -> None:
    """Data directories created before the fix hold their log inside; it must not stay there."""
    from farm.db.local import _log_path, get_local_server, stop_local

    get_local_server(crash_dir, durable=True)
    assert stop_local(crash_dir) is True
    (crash_dir / "log").write_text("old history\n", encoding="utf-8")

    server = get_local_server(crash_dir, durable=True)
    with psycopg.connect(server.get_uri(), autocommit=True) as conn:
        assert conn.execute("select 1").fetchone() == (1,)
    assert not (crash_dir / "log").exists()
    assert (_log_path(crash_dir).with_name(f"{crash_dir.name}.legacy.log")).read_text(
        encoding="utf-8"
    ) == "old history\n"


def test_local_server_recovers_from_an_interrupted_initdb(crash_dir: Path) -> None:
    """A crash during initdb leaves a data directory without global/pg_control (no cluster, so no data) and
    maybe a staging directory. The next start must clean both up and initialise a working cluster."""
    from farm.db.local import get_local_server

    (crash_dir / "base").mkdir(parents=True)
    (crash_dir / "PG_VERSION").write_text("16\n", encoding="utf-8")
    staging = crash_dir.with_name(f"{crash_dir.name}.init-1")
    (staging / "global").mkdir(parents=True)

    server = get_local_server(crash_dir)
    assert not staging.exists()
    with psycopg.connect(server.get_uri(), autocommit=True) as conn:
        assert conn.execute("select 1").fetchone() == (1,)


def test_local_server_never_deletes_a_foreign_directory(crash_dir: Path) -> None:
    from farm.db.local import get_local_server, reset_local

    crash_dir.mkdir()
    precious = crash_dir / "notes.txt"
    precious.write_text("not a database", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a PostgreSQL data directory"):
        get_local_server(crash_dir)
    with pytest.raises(RuntimeError, match="not a PostgreSQL data directory"):
        reset_local(crash_dir)
    assert precious.read_text(encoding="utf-8") == "not a database"


def test_handle_list_repair(tmp_path: Path) -> None:
    import os

    from farm.db.local import _repair_handle_list

    handles = tmp_path / ".handle_pids.json"
    _repair_handle_list(tmp_path)  # no file: nothing to do
    assert not handles.exists()
    for torn in ("", "[12", '{"a": 1}', '["x"]'):
        handles.write_text(torn, encoding="utf-8")
        _repair_handle_list(tmp_path)
        assert json.loads(handles.read_text(encoding="utf-8")) == []
    handles.write_text(json.dumps([999999, os.getpid()]), encoding="utf-8")  # one dead, one live PID
    _repair_handle_list(tmp_path)
    assert json.loads(handles.read_text(encoding="utf-8")) == [os.getpid()]
