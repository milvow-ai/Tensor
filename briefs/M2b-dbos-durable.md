# Brief M2b — durability: DBOS workflows, per-connection queues, batch routing, crash recovery

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.

## Goal
Long or multi-item work must survive crashes and never double-charge (HANDOFF §4.1 "Durability", §13 DBOS executor-id risk). Interactive single calls
keep the fast in-process path (router + gate). Batches and recipes run as **DBOS durable workflows**: each item is a step routed through the same
router; per-connection DBOS queues bound concurrency/rate across workers; the request hash is the workflow id (dedup); each worker process runs with
an explicit executor id so a restarted worker recovers its own pending workflows.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.1, §4.3 (mid-batch failure), §13 (DBOS row), `library/dbos-transact-py` (README, docs, `dbos/_dbos.py`,
`dbos/_queue.py`, `tests/` for async workflow + queue usage — use the real 3.2 API, do not guess), current `farm/resources/router.py`, `farm/context.py`.

## Owns
`farm/durable/__init__.py`, `farm/durable/runtime.py` (DBOS config + launch/shutdown inside FarmContext; system DB = same Postgres, its own schema;
`executor_id` from env `FARM_EXECUTOR_ID` default `farm-<hostname>-main`; app version pinned so recovery matches), `farm/durable/queues.py`
(one DBOS queue per connection created from the registry at launch, concurrency = connection concurrency, limiter from `rate_per_min`; refresh when
connections change), `farm/durable/batch.py` (`route_batch(ctx, capability, items, *, strategy, caller) -> BatchOutcome` as a durable workflow:
items → steps, idempotency key per item = request_hash(item); failed item on one connection is re-queued to the next eligible connection;
progress queryable), `farm/context.py` (wire DBOS launch/shutdown; keep existing API), `farm/control/cli.py` (add `farm worker` — runs the DBOS
worker loop with the configured executor id; `farm batch <capability> --file items.jsonl`), gateway: add MCP tool `batch_<capability>` generic
pattern → one tool `run_batch(capability, items, strategy?)` returning a batch id + `get_batch(batch_id)`; tests `tests/test_durable_*.py`,
`tests/test_accept_m2_recovery.py`.

## Acceptance tests
- Batch of 20 `verify_email` items over 2 pools → all done, each item's run trajectory exists, workflow id = request hash (re-submitting the same batch returns the same result without new provider calls).
- Per-connection queue concurrency: connection concurrency 2 → never more than 2 in-flight executes on it (instrumented mock).
- **Recovery:** start a worker subprocess with executor id `farm-test-1`, submit a 10-item batch whose mocked provider blocks after item 4, kill the process, restart a worker with the same executor id → the workflow resumes, all 10 items complete, completed steps are not re-executed (mock call count per item == 1), no reservation is charged twice (ledger `used` equals successful items).
- Mid-batch account failure: connection A starts returning AUTH after item 3 → remaining items finish on connection B; A marked needs_login; zero double charges.

## Done when
`scripts/check.ps1` → `RESULT: all passed` (all earlier tests green).

## Reply (≤ 15 lines)
Files changed; test count; check tail; DBOS API notes that later briefs must know (how to define/launch/enqueue); deviations.
