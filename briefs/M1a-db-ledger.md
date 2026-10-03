# Brief M1a — database, migrations, quota ledger, test DB fixtures

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.

## Goal
Give the Farm its full Postgres schema (every table in CONTEXT §3, even those used by later milestones, so the Console can be built in parallel),
the atomic quota ledger, an embedded local Postgres for dev/tests, and the DB CLI. This is the foundation every other milestone relies on: correctness of
reserve/commit/release under concurrency matters more than anything else here.

## Read first
`briefs/CONTEXT.md` (all), `HANDOFF.md` §4.5, `research/sketches/data-model-v0.sql` lines 308–348 (the tested `consume_quota` idea), `library/dbos-transact-py` is NOT needed yet.
For pgserver usage: `uv run python -c "import pgserver, inspect; print(inspect.getsource(pgserver.get_server))"` after adding the dependency.

## Owns
- `pyproject.toml`, `uv.lock` — add deps: `psycopg-pool>=3.2`, `pgserver>=0.1.4` (runtime dep: local mode); dev: `pytest-socket>=0.7`. Add pytest `addopts = "--disable-socket --allow-hosts=127.0.0.1,localhost,::1"`.
- `farm/settings.py` — add `data_dir() -> Path` (env `FARM_DATA_DIR`, default `D:/farm-data`, created on demand). Keep existing functions unchanged.
- `farm/db/__init__.py`, `farm/db/pool.py`, `farm/db/local.py`
- `farm/db/alembic.ini` (or `alembic.ini` at repo root — your choice, document it in the module docstring), `farm/db/migrations/env.py`, `farm/db/migrations/script.py.mako`, `farm/db/migrations/versions/0001_core_schema.py`, `farm/db/migrations/versions/0002_rls_supabase.py`
- `farm/resources/ledger.py`
- `farm/control/cli.py` — add `farm db migrate`, `farm db up` (start local Postgres, print `local db ready` + the port, never a password), `farm db check` (keep current behaviour but use `get_db_url()`), `farm db reset-local` (drops and recreates the LOCAL embedded db only; refuses if the URL is not local).
- `tests/conftest.py`, `tests/test_db_schema.py`, `tests/test_ledger.py`, `tests/test_accept_m1_ledger_concurrency.py`

## Steps
1. Dependencies + pytest addopts (above). `uv sync`.
2. `farm/db/local.py`: start/stop an embedded server with `pgserver.get_server(<FARM_DATA_DIR>/pg, cleanup_mode=None)` (keep it running between processes; idempotent), return its URI. Tests use a separate dir `<FARM_DATA_DIR>/pgtest`.
3. `farm/db/pool.py`: `get_db_url()` per CONTEXT §3 (FARM_DB_URL → SUPABASE_DB_URL → local). `open_pool()` returns an opened `AsyncConnectionPool` (min 1, max 10, `kwargs={"autocommit": True}`), `close_pool()`.
4. Migration `0001_core_schema`: every table in CONTEXT §3 with the listed columns, checks, FKs (`on delete cascade` from connections to their child tables), sensible indexes (runs by started_at desc, run_events by run_id+seq, usage_events by connection_id+at, capability_requests by expires_at, farm_commands by status+created_at), `updated_at` trigger, the default workspace row isn't needed (default uuid is enough), and a `farm_settings` singleton row inserted.
   SQL functions `farm_period_start`, `farm_reserve`, `farm_commit`, `farm_release`, `farm_expire_reservations` exactly as CONTEXT §3 describes. Implementation notes:
   - `farm_reserve`: look up the unit row in `consumption_units` (missing unit → raise), compute period_start, upsert the `quota_usage` row, then a single `UPDATE … SET reserved = reserved + amount WHERE … AND (limit_value IS NULL OR used + reserved + amount <= limit_value) RETURNING …`; insert the reservation only if the update hit a row; return its id, else null.
   - `farm_commit`: lock the reservation row (`FOR UPDATE`); if status is not `reserved` return (idempotent); subtract `amount` from `reserved`, add `p_actual` to `used`, set status `committed`, `actual`.
   - `farm_release`: same locking; subtract from `reserved`, status `released`.
   - `farm_expire_reservations()`: releases every `reserved` row past `expires_at`; returns count.
   - When a unit's `limit_value` changes in `consumption_units`, the next reserve must use the new limit (copy it into `quota_usage.limit_value` on upsert).
5. Migration `0002_rls_supabase`: `alter table … enable row level security` on every table; owner policies per CONTEXT §3 only inside a DO block guarded by the existence of schema `auth`.
6. `farm/resources/ledger.py`: async wrappers `reserve`, `commit`, `release`, `expire` (psycopg, parameterised).
7. `tests/conftest.py`: session fixture `db_url` (embedded pgserver in `data_dir()/pgtest`, create database `farm_test_<random>`, run `alembic upgrade head` against it programmatically, drop it at session end); fixture `pool` (opens a pool to it; before each test truncates all tables except `alembic_version` and `farm_settings` with `RESTART IDENTITY CASCADE`); helper `seed_connection(pool, provider_id, conn_id, units={...})`.
8. Tests:
   - `test_db_schema.py`: every CONTEXT §3 table exists with its key columns; RLS enabled on all; migration is re-runnable (`downgrade base` then `upgrade head` works).
   - `test_ledger.py`: reserve within limit → id; over limit → None; commit actual < amount frees the difference; commit with actual 0; release frees; double commit/release is a no-op; expired reservations released by `expire`; monthly anchor period boundaries (anchor 31 in Feb); null limit = unlimited; changing the limit applies to the next reserve.
   - `test_accept_m1_ledger_concurrency.py`: limit 50, launch 200 concurrent `reserve(amount=1)` tasks over the pool → exactly 50 succeed, `used+reserved == 50`; then commit 30 and release 20 → `used == 30`, `reserved == 0`.
9. Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1` until it passes. Also run `uv run farm db up` and `uv run farm db migrate` once (local mode) and confirm both succeed.

## Done when
`scripts/check.ps1` → `RESULT: all passed`, including the concurrency acceptance test; `uv run farm db up` + `uv run farm db migrate` succeed in local mode.

## Reply (≤ 15 lines)
Files changed; test count; last 6 lines of check.ps1; any deviation from CONTEXT §3 and why; open issues.
