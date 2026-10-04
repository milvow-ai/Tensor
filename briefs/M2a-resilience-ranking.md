# Brief M2a — Resource Manager resilience: circuit breaker, backoff, timeouts, bulkheads, ranking, strategies

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.

## Goal
Make routing production-grade (HANDOFF §4.3, §4.8 — Nygard, *Release It!*): one bad account can never stall the machine, retries never stampede,
every call has a timeout, each connection is a bulkhead, and candidates are ranked by price, reset timing and track record before the strategy
orders them. Everything is persisted in Postgres so a restart keeps circuits, cooldowns and round-robin pointers.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.3 + §4.8, the current `farm/resources/{router,health,strategies,ledger}.py`, `farm/executors/base.py`, the M1 acceptance tests.

## Owns
`farm/resources/health.py`, `farm/resources/strategies.py`, `farm/resources/ranking.py` (new), `farm/resources/gate.py` (new), `farm/resources/router.py` (integrate; keep its public API and every M1 behaviour),
a new Alembic revision `0004_rr_state.py` only if you need a table for round-robin pointers (prefer a `pool_state` table: provider_id pk, `rr_cursor int`, `updated_at`),
tests `tests/test_health.py`, `tests/test_ranking.py`, `tests/test_strategies.py` (extend), `tests/test_gate.py`, `tests/test_accept_m2_faults.py`, `tests/test_accept_m2_bulkhead.py`.

## Behaviour to implement
1. **Circuit breaker per connection (Postgres-backed):** closed → open after `N` consecutive failures of kinds SERVER/TIMEOUT/UNKNOWN (default 5, per-connection override in `meta.circuit`); open for `open_seconds` (default 120) then half-open: exactly one probe request allowed (row-locked claim), success → closed, failure → open with doubled open time (cap 30 min). AUTH → status `needs_login` + alert (no circuit). LIMIT_REACHED → status `exhausted` until `reset_at`/next period start, then auto-active again (lazy check on read + `farm_reactivate_due()` SQL function). RATE_LIMITED → cooldown `retry_after` (default 60 s, jittered ±20 %).
2. **Retries inside one connection:** SERVER and TIMEOUT get at most 1 retry on the same connection with exponential backoff + full jitter (tenacity; base 0.5 s, cap 4 s) when the capability is idempotent (flag on CapabilitySpec, default true for reads); never retry AUTH/LIMIT/BAD_REQUEST/EMPTY; then fall back to the next candidate.
3. **Timeouts:** every execute wrapped in `asyncio.timeout(timeout_s)`; timeout_s = connection `meta.timeout_s` or capability default (30 s; LLM 90 s; AI CLI 900 s).
4. **Bulkhead gate per connection** (`gate.py`): `ConnectionGate` = `asyncio.Semaphore(concurrency)` + `aiolimiter.AsyncLimiter(rate_per_min, 60)` when set; acquired around execute; a saturated connection makes the router try the next candidate if the wait would exceed `meta.max_queue_wait_s` (default 2 s) instead of blocking the whole request.
5. **Ranking** (`ranking.py`, pure, unit-tested): score candidates inside a pool by (a) price class: free (unit_cost 0) < pay-only-if-found (charged_on found) < cheapest per unit; (b) soonest reset first among equal price (spend credits that expire first); (c) success rate over the last 50 calls (from connection_health counts); (d) priority; (e) id. Return the ordered list + the reasons (written into the `candidate` run_event).
6. **Strategies** (`strategies.py`): `failover` (ranked order), `most_remaining` (highest remaining fraction first), `round_robin` (persistent cursor per pool, skips ineligible), `pin` (only the pinned connection, error if ineligible). `parallel_split`/`sticky`/`fit_check` stay for M3 — raise `NotImplementedError` with a clear message if requested now.
7. **Router integration:** apply gate, timeouts, retries, health transitions and ranking inside the existing flow; every decision visible in `run_events` (`skip` with reason: circuit_open, cooldown, exhausted, needs_login, paused, saturated, scope, budget).

## Acceptance tests
- `test_accept_m2_faults.py`: for each fault 429 / 401 / empty / timeout / 500 / malformed on the first connection → correct ErrorKind, correct health transition, fallback to the second pool, reservations consistent (none left `reserved`), and **0 real network calls** (respx `assert_all_mocked`).
- Circuit: 5 consecutive 500s open it; requests during open skip it; after `open_seconds` (injected clock) exactly one half-open probe; success closes it.
- `test_accept_m2_bulkhead.py`: connection A (concurrency 1) is held busy by a slow mocked call; 5 concurrent requests are not delayed beyond `max_queue_wait_s` — they are served by connection B; A's slowness never blocks B (measure with the injected clock or tight wall-clock bounds).
- Ranking/strategy unit tests for every rule above, including round-robin persistence across a new `FarmContext`.

## Done when
`scripts/check.ps1` → `RESULT: all passed` (all M1 tests still green).

## Reply (≤ 15 lines)
Files changed; test count; check tail; deviations and open issues.

## Added 2026-10-04 (open issues from M1c — also in scope)
1. **Capability → unit reservation map:** today the router reserves every unit with `estimate_per_call > 0`, so a Hunter `find_email` also reserves `verifications`. Add a per-capability unit map (CapabilitySpec or connection `meta.units_by_capability`, e.g. `{find_email: {searches: 1}, verify_email: {verifications: 1}}`); reserve only mapped units; fall back to all units only when no map exists. Test with Hunter's two units.
2. **Per-connection timeouts from config:** honour `connection.meta.timeout_s` / provider `config.timeout_s`; set Reoon to 90 s in `config/registry.yaml` (power mode can exceed a minute).
3. **Crash hygiene:** runs left `running` by a crashed process are finalised as `failed` with `error_kind=interrupted` at startup (and their reservations released via `farm_expire_reservations`); expired `capability_requests` cache rows are purged by a periodic job (wire into the manager scheduler).
4. **Cost bookkeeping:** an executor's own `cost_usd` must land in `usage_events.cost_usd` (not only `runs.cost_usd`), so Billing/budgets see it.
