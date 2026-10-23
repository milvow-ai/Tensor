# F1A — Tenancy schema (first step of the SaaS foundation)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. **Never run a
migration against a live database** (`FARM_DB_URL`, `SUPABASE_DB_URL`, or `D:/farm-data/pg`): tests use only the test database
from `tests/conftest.py`. Run every command in the foreground and wait for it.

**Goal.** Make tenants real rows and give every tenant-owned table a `tenant_id` linked to them, without breaking the running
single-tenant Farm. Milvow becomes tenant #1 with the id every existing row already carries. This is the *expand* step; code
plumbing and RLS enforcement come in F1B.

**Read first:** `briefs/CONTEXT.md` (§3, including the F1 change box) · `docs/ARCHITECTURE.md` §4, §7 and ADR 1, 2, 12 ·
`farm/db/migrations/versions/0001_core_schema.py` and `0013_integration_requests.py` (style) · `farm/db/pool.py` ·
`tests/conftest.py` · `tests/test_db_schema.py`.

**Already known:**
- Core tables have `workspace_id uuid not null default '00000000-0000-0000-0000-000000000001'` (0001); 0007 and 0008 tables also
  have it; some later tables do not (check `pool_state` from 0005, `integration_requests` from 0013 and every other table —
  enumerate from `information_schema`, do not guess).
- `workspace_id` appears in code in `farm/mcp/store.py` and `farm/resources/flight.py`, and in tests in
  `tests/fake_mcp_server.py`, `tests/test_ai_jobs.py`, `tests/test_db_schema.py`. The Console does not use it.
- `capability_requests` has `unique(workspace_id, request_hash)`.

**Steps:**
1. Migration `0014_tenancy.py` (raw SQL through `op.execute`, like the others):
   - `tenants` (`id uuid pk default gen_random_uuid()`, `slug text unique not null`, `name text not null`,
     `cell_id text not null default 'cell-1'`, `plan text not null default 'beta'`,
     `status text not null default 'active' check (status in ('active','suspended','deleted'))`, `created_at`, `updated_at`);
     insert tenant #1: id `00000000-0000-0000-0000-000000000001`, slug `milvow`, name `Milvow`.
   - `users` (`id uuid pk default gen_random_uuid()`, `email text not null` with a unique index on `lower(email)`,
     `display_name text`, `auth_subject text unique`, `locale text not null default 'en'`,
     `timezone text not null default 'UTC'`, `created_at`, `last_seen_at`).
   - `memberships` (`tenant_id` → tenants on delete cascade, `user_id` → users on delete cascade,
     `role text not null check (role in ('owner','admin','member'))`, `created_at`, primary key (`tenant_id`, `user_id`)).
   - Rename `workspace_id` → `tenant_id` on every table that has it (a `DO` block over `information_schema`, schema `public`).
   - Add `tenant_id uuid not null default '00000000-0000-0000-0000-000000000001'` to every other tenant-owned table. Platform
     tables without it: `alembic_version`, `tenants`, `users`. `farm_settings` is per tenant.
   - Add an FK `tenant_id → tenants(id)` on every tenant-owned table, and an index whose first column is `tenant_id` where
     none exists.
   - Keep the defaults for now, with a SQL comment saying F1B removes them once code passes the tenant explicitly. Do not change
     RLS policies or database roles in this step.
   - Downgrade: drop the FKs and the new tables, rename back.
2. `farm/tenancy/__init__.py` + `farm/tenancy/models.py`: Pydantic models `Tenant`, `User`, `Membership`, a `Role` StrEnum, a frozen
   `TenantContext(tenant_id: UUID, user_id: UUID | None, roles: frozenset[Role], cell_id: str)`, and `MILVOW_TENANT_ID`. Add
   `async load_tenant(pool, tenant_id)` only if the tests need it. No other DB code yet.
3. Rename the `workspace_id` references in `farm/mcp/store.py` and `farm/resources/flight.py` (those lines only) and in the three
   test files.
4. `tests/test_f1a_tenancy.py`: at head, every table except the platform list has `tenant_id uuid not null`, an FK to `tenants`
   and a tenant-first index; tenant #1 exists; an insert without `tenant_id` still lands in tenant #1; `unique(tenant_id,
   request_hash)` holds; downgrade then upgrade round-trips on a test database that has rows in it.
5. Run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1`.

**Owns:** `farm/db/migrations/versions/0014_tenancy.py` (new) · `farm/tenancy/**` (new) · `farm/mcp/store.py` and
`farm/resources/flight.py` (the `workspace_id` lines only) · `tests/fake_mcp_server.py`, `tests/test_ai_jobs.py`,
`tests/test_db_schema.py` (`workspace_id` references only) · `tests/test_f1a_tenancy.py` (new).

**Must not touch:** earlier migrations, `farm/gateway/**`, `farm/executors/**`, `farm/ai/**`, `config/*`, `console/`,
`pyproject.toml`, `uv.lock`, any other file.

**Done when:** `check.ps1` ends with `RESULT: all passed`; the migration applies cleanly both to a fresh test database and to one
at revision 0013 that holds data.

**Reply (15 lines or fewer):** tables renamed / added / left as platform tables · files changed · `check.ps1` tail · open issues.
