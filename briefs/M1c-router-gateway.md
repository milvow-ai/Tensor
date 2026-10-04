# Brief M1c — router, trajectory, cache, single-flight, MCP gateway (walking skeleton end to end)

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.

## Goal
Make the first capability work end to end exactly as HANDOFF §4.1 describes: Claude calls the MCP tool `verify_email`, the Farm checks the cache,
collapses concurrent identical requests into one, picks a pool (Reoon, then ZeroBounce) and a connection, reserves quota, executes, commits or
releases, falls back on failure, writes the run trajectory, and returns the standard envelope (CONTEXT §4). Every later capability reuses this path,
so the router must be generic (no `verify_email` special cases outside the capability table).

## Read first
`briefs/CONTEXT.md` (all), `HANDOFF.md` §4.1–§4.3, §4.6, §4.8. Code already in the repo: `farm/db/*`, `farm/resources/ledger.py`, `tests/conftest.py` (M1a);
`farm/registry/*`, `farm/executors/base.py`, `farm/adapters/*`, `farm/capabilities/schemas.py`, `farm/secrets.py` (M1b). FastMCP 4 API: read
`library/fastmcp/docs` (servers/tools, middleware, client in-memory transport, testing) — do not guess the middleware API.

## Owns
- `farm/context.py` — `FarmContext` (pool, registry, executor registry, clock injectable for tests) + `async build_context()`.
- `farm/registry/sync.py` — `sync_registry(pool, registry)` (upsert providers, connections, consumption_units, capabilities, capability_routes, budgets, farm_settings; writes `audit_events`), `export_registry(pool) -> Registry` (DB → YAML round trip).
- `farm/executors/api.py` — `ApiExecutor` dispatching `ExecRequest` to `ADAPTERS[provider_id]`.
- `farm/resources/health.py` (M1 subset: record success/failure into `connection_health`; RATE_LIMITED → `cooldown_until = now + retry_after (default 60 s)`; LIMIT_REACHED → cooldown until `reset_at` or next period start and connection status `exhausted`; AUTH → status `needs_login`; circuit opens after 5 consecutive failures for 120 s — M2 will extend).
- `farm/resources/strategies.py` (M1: `failover` and `pin`; a pure function `order_candidates(candidates, strategy, pin) -> list`).
- `farm/resources/router.py` — `route(ctx, capability, params, *, strategy=None, pin=None, caller)` → `RouteOutcome` (envelope fields). Flow: validate params against the capability input model → request_hash → cache (`capability_requests` succeeded & not expired → return with `cached=True`, cost 0, run event `cache_hit`) → single-flight (in-process `dict[hash, asyncio.Future]`; cross-process via the unique `capability_requests` row: if another process holds it `running`, poll up to the timeout) → policy (M1: block connections whose `unit_cost_usd > 0` while the applicable budget is 0 → `policy_block` event) → candidates in route order (pool by pool; inside a pool by strategy; skip disabled/paused/needs_login/exhausted, open circuit, active cooldown, with a `skip` event and reason) → `farm_reserve` for every unit with `estimate_per_call` (fail → `reserve_failed`, next) → execute with timeout → success: `farm_commit(actual from units_used, honouring charged_on: attempt|success|found)`, usage_events, health ok, cache write (`cache_ttl_seconds`) → failure: commit-or-release per charged_on, health failure, `fallback` event, next candidate → all failed: envelope `ok=false` with the last error kind and every attempt summarised. Every step writes `run_events` with increasing `seq`; `runs` row finalised with status, cost, final connection.
- `farm/gateway/__init__.py`, `farm/gateway/server.py`, `farm/gateway/middleware.py` — FastMCP server `harness-farm`; tools generated from the capability table + the CapabilitySpec input models (M1: `verify_email`), infra tools `get_capacity(capability?)` (per pool/connection: status, circuit, cooldown, remaining per unit, next reset), `list_resources()`, `get_run(run_id)` (trajectory), `get_usage(days=30)`. Middleware: auth (stdio = local trust; HTTP bearer token from `env:FARM_MCP_TOKEN` for later), policy hook, trajectory (sets `caller` from client info). No admin tools exposed to agents.
- `farm/control/cli.py` — add: `farm serve` (stdio), `farm registry sync [path]`, `farm registry export [path]`, `farm registry schema > file`, `farm call <capability> --params '<json>'`, `farm status` (table of pools/connections/health/remaining). Keep everything M1a added.
- `scripts/mcp_smoke.py` — spawns `uv run farm serve` over stdio with `fastmcp.Client`, lists tools, asserts `verify_email`, `get_capacity`, `list_resources`, `get_run`, `get_usage` exist; exits 0/1. (No provider call.)
- Tests: `tests/test_router.py`, `tests/test_strategies.py`, `tests/test_registry_sync.py`, `tests/test_gateway.py`, and acceptance tests `tests/test_accept_m1_failover.py`, `tests/test_accept_m1_single_flight.py`, `tests/test_accept_m1_cache.py`, `tests/test_accept_m1_mcp.py`. You may add fixtures to `tests/conftest.py` (append only; do not change M1a's fixtures).

## Acceptance tests (must exist and pass; all HTTP via respx, keys via monkeypatched env)
1. **Failover on exhausted quota:** Reoon `credits` used = limit → reserve fails → ZeroBounce answers; ZeroBounce reservation committed; Reoon has no reservation; run_events contain `reserve_failed`(reoon) then `success`(zerobounce); run status succeeded.
2. **Fallback on provider error:** Reoon returns 500 → its reservation released (charged_on success), health failure recorded; ZeroBounce succeeds; respx shows exactly 1 Reoon + 1 ZeroBounce call.
3. **Rate limit cooldown:** Reoon 429 Retry-After 120 → `cooldown_until` ≈ now+120 s; an immediate second (different) request skips Reoon with reason `cooldown`.
4. **Single-flight:** 10 concurrent identical `verify_email` calls → exactly 1 provider HTTP call, 1 committed reservation, 10 identical results, run events `single_flight_join` for 9.
5. **Cache:** second identical call after success → no HTTP call, `cached=true`, cost 0, `cache_hit` event; after TTL expiry (inject clock) → calls the provider again.
6. **All fail:** both pools fail → `ok=false`, error kind of the last attempt, no reservation left in `reserved` state.
7. **MCP:** in-memory `fastmcp.Client(server)` lists the tools and calls `verify_email` → envelope matches CONTEXT §4; `get_run(run_id)` returns the trajectory; `get_capacity` shows the decrement.
8. **Registry round trip:** `sync_registry(config/registry.yaml)` then `export_registry` equals the loaded registry (order-insensitive).

## Done when
`scripts/check.ps1` → `RESULT: all passed`; `uv run python scripts/mcp_smoke.py` exits 0; `uv run farm registry sync` and `uv run farm status` work in local mode.

## Reply (≤ 15 lines)
Files changed; test count; last 6 lines of check.ps1; mcp_smoke output; deviations from CONTEXT and why; open issues.

## Added 2026-10-04 (after M1a)
- **Windows event loop:** psycopg async requires a `SelectorEventLoop`; the Farm process (`farm serve`, `farm call`, tests) must run on it (set the policy once at process start in the CLI entry points; the test conftest already handles the DB loop). Do not use `asyncio.create_subprocess_*` anywhere in your code (it needs the Proactor loop on Windows); the cli_agent runner is being moved to thread-based subprocesses by a parallel fix brief.
- The test fixtures are in `tests/conftest.py` (M1a): `db_url`, `pool`, `registry`, `seed_connection` — reuse them.
