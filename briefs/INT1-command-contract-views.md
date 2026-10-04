# Brief INT1 — one command contract (Farm → Console) + Console views as an Alembic migration

Rules: follow `briefs/CONTEXT.md` §6 and §0. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`/`console/.env.local`.

## Why
The Console (C1) and the Farm's command consumer (`farm/control/commands.py`, M3b) were built in parallel and disagree on payloads: e.g. the Console
sends `add_connection` as `{provider_id, connection:{…}}`, the consumer expects flat fields; unit field names may differ too. A Console button that
the Farm silently rejects is the worst kind of bug. Fix it structurally: **the Farm's Pydantic payload models are the single source of truth**; the
Console's payload builders are tested against JSON Schemas exported from them, so any future drift fails a test. Also the Console's SQL views
(`console/sql/views.sql`) must become a real migration so `farm db migrate` creates them on Supabase and in local mode.

## Read first
`briefs/CONTEXT.md`, `farm/control/commands.py` (payload models for all 11 kinds), `console/README.md` (command table), `console/src/lib/farm/commands.ts`
(zod schemas + builders), `console/src/lib/farm/actions.ts`, `console/sql/views.sql`, `farm/db/migrations/versions/*` (current head), `tests/conftest.py`.

## Owns
`farm/control/commands.py` (add `export_command_schemas() -> dict[kind, JSON Schema]`; you may adjust models only to make the contract coherent — e.g. accept the unit shape `{limit, period, anchor, charged_on, unit_cost_usd, estimate_per_call}` mapped to `consumption_units` columns — never loosening validation), `farm/control/cli.py` (add only `farm commands schema [--out path]`), `console/src/lib/farm/command-schemas.json` (generated), `console/package.json` (script `schema:commands` that regenerates it via `uv run farm commands schema --out …`; devDep `ajv` if needed), `console/src/lib/farm/commands.ts` + `actions.ts` (change payload shapes to the Farm contract, e.g. flat `add_connection`), console unit test `console/src/lib/farm/__tests__/commands.contract.test.ts` (every builder's output for every kind validates against the exported schema; a deliberately wrong payload fails), `console/README.md` command table (regenerate from the contract), `farm/db/migrations/versions/0003_console_views.py` (wraps `console/sql/views.sql`; downgrade drops the views), `tests/test_console_views.py` (seed a provider/connections/usage/runs with the `pool` fixture and assert each view's key numbers), `tests/test_command_contract.py` (round trip: every Console example payload in `console/src/lib/farm/__tests__/fixtures/*.json` — generate them from the builders — is accepted by the consumer and executes against the test DB).

## Done when
`scripts/check.ps1` → RESULT: all passed; in `console/`: `pnpm check` and the contract test pass (`pnpm test` or vitest), and `pnpm test:e2e` still passes; `uv run farm db migrate --local` applies 0003.

## Reply (≤ 12 lines)
Files changed; which payloads changed shape; test counts; check tails; deviations.
