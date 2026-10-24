# F1C — Per-tenant keys: two tenants can use the same ids (D45)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. Never run a migration
against a live database. Run every command in the foreground; run ruff, mypy and the relevant test files yourself (the gate runs
the full suite after you; the full suite needs a lot of memory on this PC — never run two full suites at once).

**Goal.** Today ids are global: `providers.id`, `connections.id`, `capabilities.name`, `farm_settings` (singleton), and other text
keys and global uniques (e.g. `mcp_tools`, `pool_state`, `entities.canonical_key`, `evidence.sha256`). Two tenants cannot both have
an account called `claude-01` or sync the same registry. Make every tenant-owned key per tenant, and make foreign keys carry the
tenant so a row can never reference another tenant's row. `docs/QUALITY.md` D45; `docs/ARCHITECTURE.md` §4.

**Read first:** `briefs/CONTEXT.md` · `docs/ARCHITECTURE.md` §4 (three fail-closed layers) · `docs/QUALITY.md` D45 ·
`farm/db/migrations/versions/0001_core_schema.py`, `0014_tenancy.py`, `0019_tenancy_enforced.py` · `farm/registry/sync.py`,
`farm/registry/writer.py` · `farm/mcp/store.py` · every `on conflict` in `farm/` (grep) · `tests/test_isolation_*.py`.

**Key point:** with RLS on, a lookup such as `where id = $1` already sees only the current tenant's rows, so most reads need no
change. What changes: primary keys, unique constraints, foreign keys, and every `insert … on conflict (<old key>)`.

**Steps:**
1. **Inventory** (write it at the top of the migration as a comment): every table in `public` with its current primary key, unique
   constraints and foreign keys; mark which are tenant-owned.
2. **Migration `farm/db/migrations/versions/0020_tenant_keys.py`** (`down_revision = "0019"`, one transaction, full downgrade):
   - tenant-owned tables: primary key becomes `(tenant_id, <old key columns>)`; global unique constraints become
     `(tenant_id, …)` (including `entities.canonical_key`, `evidence.sha256`, `mcp_tools`, `pool_state`);
   - foreign keys between tenant-owned tables become composite `(tenant_id, x) → (tenant_id, id)` so cross-tenant references are
     impossible;
   - `farm_settings`: one row per tenant, primary key `tenant_id`;
   - keep RLS policies and grants from 0019 working (re-grant if a recreate drops them).
3. **Code:** change every `on conflict (<old key>)` to the new key; registry sync, MCP store, command handlers (`add_connection`,
   `update_connection`, …), ledger / quota upserts, AI sessions — wherever an upsert names the old key. No other behaviour change.
4. **Tests** `tests/test_f1c_*.py`: two tenants each sync the **same** registry YAML (same provider and connection ids) and both
   work: routing, quota reservation, health, AI-job queueing, MCP tool catalog, commands — each sees only its own rows and its own
   counters; a foreign key cannot point at another tenant's row (insert must fail); `farm_settings` per tenant. Extend the isolation
   suite so its two tenants use identical ids from now on.

**Owns:** the new migration 0020 · `farm/registry/{sync,writer}.py` · `farm/mcp/store.py` · the `farm/**` files whose upserts name an
old key (list each in the reply) · `tests/test_f1c_*.py` (new) · `tests/test_isolation_*.py` · `tests/conftest.py` (only if fixtures
need the new keys) · existing tests that relied on global ids (list each).

**Must not touch:** earlier migrations, `farm/db/pool.py`, `farm/tenancy/**`, `config/*`, `console/`, `pyproject.toml`, any other file.

**Done when:** ruff + mypy clean; your tests and the isolation suite pass with identical ids across two tenants; the gate passes
after you.

**Reply (15 lines or fewer):** tables re-keyed · foreign keys made composite · upserts changed (files) · test results · open issues.
