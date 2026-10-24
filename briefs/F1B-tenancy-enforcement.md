# F1B — Tenant isolation enforced (runtime role, RLS, tokens, isolation suite)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. **Never run a
migration against a live database.** Run every command in the foreground; the full `check.ps1` exceeds your 10-minute command
limit, so run ruff, mypy and the relevant test files yourself — the gate runs the full suite after you.

**Goal.** After F1A every row has a `tenant_id`, but nothing enforces it: the Farm connects as the database owner and bypasses
RLS. F1B makes isolation real with the three fail-closed layers in `docs/ARCHITECTURE.md` §4 (read the revised text), and adds
the isolation test suite that proves tenant A can never read or change tenant B's data through any path.

**Read first:** `briefs/CONTEXT.md` · `docs/ARCHITECTURE.md` §4, §7, ADR 1, 2, 10 · `docs/QUALITY.md` D34 ·
`farm/db/pool.py` · `farm/context.py` · `farm/gateway/middleware.py` · `farm/control/run.py` (tokens: `create_token`,
`list_tokens`, the runtime `create table client_tokens`) · `farm/tenancy/*` (F1A) · `farm/db/migrations/versions/0014_tenancy.py`,
`0002_rls_supabase.py` (current RLS style) and the latest migration on the branch.

**Design (decided — implement exactly):**
1. **Roles** (migration): `farm_app` (LOGIN not required here — it is a role the Farm `SET ROLE`s into after connecting, so no
   new credentials are needed; no BYPASSRLS) and `farm_system` (BYPASSRLS, for maintenance only). Grant the minimum privileges
   each needs on all tenant tables, sequences and functions.
2. **Tenant on the connection:** `farm/db/pool.py` gets `tenant_connection(pool)` (async context manager): reads the
   `current_tenant` ContextVar (new, in `farm/tenancy/context.py`); if unset, raises `TenantContextMissing`; else checks out a
   connection, runs `set role farm_app` and `select set_config('app.tenant_id', $tenant, false)`, yields, and on exit runs
   `reset role` and `reset app.tenant_id`. Add `system_connection(pool)` that does `set role farm_system` for marked
   maintenance code. The pool's `reset` callback also resets both, as a second guard.
3. **Defaults and policies** (migration): every tenant-owned table: `tenant_id` default becomes
   `current_setting('app.tenant_id', true)::uuid` (no constant); enable RLS and add a policy for `farm_app`
   `USING (tenant_id = current_setting('app.tenant_id', true)::uuid) WITH CHECK (same)`. Keep the existing Supabase policies for
   the Console working (`authenticated`), then change them to membership-based: a user sees rows of tenants they belong to
   (`memberships` joined to `users.auth_subject = auth.uid()`), guarded by the existing `if exists auth schema` pattern so local
   Postgres still works. Set `security_invoker = on` on every view (`alter view … set (security_invoker = on)`).
4. **Tokens (D34):** new table `api_tokens` (`id`, `tenant_id`, `user_id` null, `name`, `token_prefix`, `token_hash` = sha256,
   `scopes text[]`, `created_at`, `last_used_at`, `revoked_at`); migrate existing `client_tokens` rows into it for tenant #1
   (hash them if they are stored in plain text), drop `client_tokens`, and remove the runtime `create table` from `run.py`.
   Auth resolves `Bearer` → `api_tokens` by hash (constant-time compare kept) → sets `current_tenant` (and user) for the request.
5. **Entry points set the tenant:** gateway auth middleware (per request), the AI job runner (per job, from the job row), the
   command consumer (per command), the router's background tasks (inherit the ContextVar — check `asyncio.create_task`
   copies context), stdio `farm serve` (tenant #1). The scheduler's cross-tenant maintenance uses `system_connection`.
6. **Switch code to the new checkouts:** replace direct `pool.connection()` uses in request-scoped code with
   `tenant_connection(pool)`, and in maintenance code with `system_connection(pool)`. Use grep to find every use; list any you
   deliberately leave on the owner connection (migrations, test fixtures) in your reply.
7. **Isolation suite** `tests/test_isolation_*.py`: two tenants with identical data shapes. For every MCP tool, every command
   kind, every Console view, and each repository function that reads or writes tenant data: acting as tenant A, (a) reading B's
   ids returns nothing / not found, (b) writing with B's ids fails, (c) inserting without a tenant fails. Generate the list of
   tools / command kinds / views from the code and the database at test time, so a new one without a test fails the suite.

**Database changes:** one migration `farm/db/migrations/versions/0019_tenancy_enforced.py` with `down_revision = "0018"`
(Q0G owns 0018 and merges before you — if 0018 is not yet on the branch when you start, chain to the latest revision you find and
say so in the reply). Full downgrade.

**Owns:** `farm/db/pool.py` · `farm/context.py` · `farm/tenancy/**` · `farm/gateway/middleware.py` · `farm/control/run.py` (tokens and
the runtime table only) · the connection-checkout call sites you switch in `farm/**` (list every file in the reply) · the new
migration · `tests/test_isolation_*.py` (new) · `tests/test_f1b_*.py` (new) · `tests/conftest.py` (only what tenant-aware fixtures
need) · existing tests that assumed owner access (list each).

**Must not touch:** earlier migrations, `config/*`, `console/` source files, `pyproject.toml`, the files Q0G owns
(`farm/manager/budgets.py`, `farm/resources/ledger.py`), any other file.

**Done when:** ruff + mypy clean; your new tests and every test file you touched pass; the gate's full suite passes after you;
the isolation suite enumerates tools, command kinds and views dynamically.

**Reply (15 lines or fewer):** roles and policies created · how entry points set the tenant · files switched to the new checkouts
· token migration result · isolation-suite counts (tools / commands / views covered) · open issues.
