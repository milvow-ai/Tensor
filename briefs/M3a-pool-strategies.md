# Brief M3a — multi-account pool strategies: parallel_split, sticky, fit_check, re-queue with idempotency

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.

## Goal
Pools of N accounts per provider (e.g. the owner's 7 paid Clay accounts) behave as one large, unstoppable resource (HANDOFF §4.3):
split batches across all eligible accounts within each account's concurrency, keep a job on one account when the provider requires it (Clay tables
live per workspace), pick the account that can finish a whole job, and when an account dies mid-batch re-queue its remaining items elsewhere with
zero double charges.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.3, `farm/resources/{router,strategies,ranking,gate}.py`, `farm/durable/batch.py` + its tests (DBOS usage notes in the M2b reply recorded in `.orchestrate/state.md`).

## Owns
`farm/resources/strategies.py` (add `parallel_split`, `sticky`, `fit_check`), `farm/durable/batch.py` (extend for split/sticky/fit and re-queue), a migration `0005_job_affinity.py` (table `job_affinity(job_key text pk, connection_id, created_at, expires_at)` for sticky jobs), router changes only where needed (keep public API), tests `tests/test_pool_strategies.py`, `tests/test_accept_m3_pool.py`.

## Behaviour
- `parallel_split`: distribute items across all eligible connections proportionally to `min(remaining capacity, concurrency × throughput)`; each connection processes its share through its own queue/gate; items carry their idempotency key (request hash); a connection that fails with AUTH/LIMIT_REACHED/circuit-open hands **all its unstarted items** back to the pool for redistribution; in-flight items finish or are released, never charged twice.
- `sticky`: the caller passes `job_key` (default = batch id); the first item picks a connection by ranking, all later items of that job go to it (`job_affinity`); if that connection becomes ineligible the job fails over as a whole to one new connection (event `requeue` with reason), never splitting a sticky job.
- `fit_check`: estimate total units for the job (`estimate_per_call × items`) and choose the best-ranked connection whose remaining capacity covers it; if none can, fall back to `parallel_split` when the capability allows splitting, else error `no_account_can_fit` with the per-account remaining list.

## Acceptance test (`test_accept_m3_pool.py`) — the HANDOFF M3 check
Simulated 7-account Clay pool (fake MCP/API executor with per-account credits 20 each, concurrency 2): a 70-item `parallel_split` batch;
kill account clay-03 (start returning AUTH) after it processed 4 items → batch completes 70/70; clay-03 → needs_login; **sum of committed credits == 70**,
no item executed successfully twice (mock records), no reservation left `reserved`. Plus: sticky job of 10 items stays on one account; when that account
hits LIMIT_REACHED at item 6, items 6–10 move together to exactly one other account. fit_check picks the account with enough credits for 15 items.

## Done when
`scripts/check.ps1` → `RESULT: all passed`.

## Reply (≤ 15 lines)
Files changed; test count; check tail; deviations.
