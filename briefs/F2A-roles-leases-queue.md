# F2A — Process roles, global leases and a multi-server job queue

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. Never run a migration
against a live database. Run every command in the foreground; run ruff, mypy and the relevant test files yourself (the gate runs
the full suite after you).

**Goal.** Today `farm run` is one process doing everything, and limits are enforced only inside that process
("connections.concurrency is enforced per Farm process", `farm/ai/jobs.py`). The Farm must grow by adding servers. Split it into
roles that share state only through Postgres, with global leases and a queue that several worker processes can drain safely.
`docs/ARCHITECTURE.md` §3, §6, ADR 3, 4, 12.

**Read first:** `briefs/CONTEXT.md` · `docs/ARCHITECTURE.md` §3, §4 (tenant checkout — F1B), §6 · `docs/audits/Q0-audit.md`
Q0-18, Q0-19 · `docs/QUALITY.md` D38 · `farm/control/run.py` · `farm/ai/jobs.py` · `farm/control/commands.py` (CommandConsumer,
the Q0E sweeps) · `farm/manager/scheduler.py` · `farm/control/heartbeat.py` · `farm/db/pool.py` (`tenant_connection`,
`system_connection` from F1B).

**Build:**
1. **Leases** — table `leases(resource_key text primary key, holder text not null, tenant_id uuid null, acquired_at, expires_at)`
   and `farm/platform/leases.py`: `acquire(key, holder, ttl)` (insert … on conflict do update where expired, returning — atomic),
   `renew`, `release`, and a `held(key)` helper. Holder = `<host>:<pid>:<role>:<uuid>`. Fencing: every renew/release checks the holder,
   so a stalled process that lost its lease cannot act on it (Q0-18).
2. **Global limits** — per-account `max_parallel` (one session per CLI login), per-pool caps and per-tenant concurrency become lease
   slots (`acct:<connection_id>:<n>`, `pool:<provider>:<n>`, `tenant:<id>:<n>`), not in-memory counters (Q0-19). Keep the in-process
   fast path only as an optimisation on top.
3. **Queue** — `ai_jobs` claiming becomes `update … set state='running', owner=…, lease_until=… where id = (select id from ai_jobs where
   state='queued' and <eligible> order by priority, created_at for update skip locked limit 1) returning …`, per tenant fairness
   (round-robin across tenants with queued work). A `QueuePort` interface wraps it so pgmq / DBOS can replace it later.
4. **Leader election** — `farm/platform/leader.py`: one scheduler leader via `pg_try_advisory_lock` on a dedicated connection, with
   loss detection; only the leader runs maintenance (expire reservations, reactivation, budgets, renewals, the Q0E command and run
   sweeps — move those out of `CommandConsumer` into the scheduler), using `system_connection`.
5. **Roles** — new CLI commands `farm api` (MCP + health), `farm worker` (job runner), `farm scheduler` (leader-elected
   maintenance), `farm consumer` (owner commands); `farm run` stays as all-in-one and starts all four in one process. Each role
   writes a heartbeat row (`role_heartbeats(role, holder, tenant null, at)`), shown by `farm status`.
6. **Cells** — `tenants.cell_id` exists (F1A); add `FARM_CELL_ID` (default `cell-1`): a process only serves tenants of its cell.
7. **D38** — find why the Farm closes during MCP start-up under load in `test_accept_aip2 … kills_the_worker`; fix the cause if it
   is in start-up ordering, then delete its line from `tests/quarantine.txt` (prove 20/20 under parallel load).

**Tests:** three worker processes and 200 jobs → no job runs twice, no account ever exceeds `max_parallel`, every job finishes;
kill a worker mid-job → its lease expires and the job is retried or failed per the existing rules, never run twice concurrently;
three scheduler processes → maintenance runs exactly once per tick; a stalled lease holder cannot renew or release after expiry.

**Database changes:** one migration `farm/db/migrations/versions/0021_f2a_leases_roles.py`, `down_revision = "0020"` (F1C), full
downgrade.

**Owns:** `farm/platform/**` (new) · `farm/control/run.py` · `farm/control/cli.py` (new role commands only) · `farm/ai/jobs.py` ·
`farm/control/commands.py` (moving the sweeps out only) · `farm/manager/scheduler.py` · `farm/control/heartbeat.py` · the new migration ·
`tests/test_f2a_*.py` (new) · `tests/test_accept_aip2.py` + its line in `tests/quarantine.txt` · existing tests that asserted
per-process limits (list each).

**Must not touch:** `farm/db/pool.py`, `farm/tenancy/**`, `farm/gateway/**`, `farm/secrets.py`, `farm/registry/**`, storage files
(F2B), earlier migrations, `config/*`, `console/`, any other file.

**Reply (15 lines or fewer):** what each role runs · lease keys used · race-test results · D38 cause and fix · migration · open issues.
