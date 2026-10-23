# Q0D — Fix the router, health, ledger, budget and scheduler findings (quality baseline)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. Never run a
migration against a live database. Run every command in the foreground and wait for it (the full check takes ~20 minutes).

**Goal.** Close the audit findings in the routing core so the foundation is built on correct behaviour. Fix each at its root;
turn each reproduction into a permanent regression test that fails on the old code and passes on the new.

**Read first:** `briefs/CONTEXT.md` · `docs/audits/Q0-audit.md` (the rows named below, in full) · the reproductions in
`D:/dev-cache/runs/q0c/repro/test_q0c_repro.py` · `docs/QUALITY.md` D7 · the code each finding cites.

**Findings to fix:**
- **Q0-01 (critical)** half-open circuit stuck for ever: every probe outcome must resolve the half-open state (success →
  closed; any failure, empty, rate limit, auth, cancellation or shutdown → open again with a fresh cooldown, or released back to
  half-open on cancellation so the next call probes). Write the state transition table in a docstring and test every row.
- **Q0-04** budget hard stop races: reserve spend before a paid call (estimate × unit cost) and settle it after, the same way
  units are reserved, so concurrent calls cannot overshoot a hard stop. Test: five concurrent $0.50 calls against $1.00 → two
  pass, three are blocked.
- **Q0-05** scheduler resumes exhausted accounts on a stale `next_reset_at` and ignores `cooldown_until`: clear / advance
  `next_reset_at` when a window resets, and never reactivate while `cooldown_until` is in the future.
- **Q0-07** cross-process single-flight: a waiter must receive the leader's outcome even when it is not cached (TTL 0 or
  `found = false`) — keep the outcome readable for joiners for a short grace period, independent of the cache TTL.
- **Q0-08** `failover` must follow `priority` first; success history only breaks ties, and over the last 50 outcomes as the
  docstring says, not lifetime.
- **Q0-12** `most_remaining` must rank by the real remaining fraction (load it).
- **Q0-13** commit after an expired reservation must not be silent: record the actual usage (late commit) and log a warning.
- **Q0-14** `rolling_5h` must be a real rolling window, not a lifetime counter.
- **Q0-21** close the window where a reservation can leak between reserve and the settling `try`.
- **Q0-22** cache only answers with `found is True`.
- **Q0-23** remove the debug `string_agg` / `RAISE NOTICE` from `farm_reactivate_due`.
- **Q0-24** `record_failure` must not hold one pooled connection while taking another (no pool-exhaustion deadlock).
- **D7 (your two quarantined tests)** make `test_accept_m2_bulkhead::test_bulkhead_slow_connection_a_bypassed_to_b_within_max_queue_wait`
  and `test_adapter_zerobounce::test_http_status_maps_to_error_kind[json-429-rate_limited]` deterministic under CPU load
  (controlled time or events, never a bigger timeout), then delete their two lines from `tests/quarantine.txt`. Prove it by
  running each 20 times in a row while the full suite runs in parallel.

**Database changes** go in one new migration `farm/db/migrations/versions/0015_q0_router_fixes.py` with
`down_revision = "0014"` (raw SQL, like the others).

**Owns:** `farm/resources/{router,health,strategies,ranking,ledger,flight,budget,periods,gate}.py` · `farm/manager/scheduler.py`
· `farm/manager/pacing.py` · the new migration 0015 · `tests/test_q0d_*.py` (new) · `tests/test_accept_m2_bulkhead.py` ·
`tests/test_adapter_zerobounce.py` · `farm/adapters/_template.py` and `farm/adapters/zerobounce.py` (only if the D7 fix needs
them) · your two lines in `tests/quarantine.txt` · existing
tests in `tests/` that asserted the old, wrong behaviour (list each change in the reply).

**Must not touch:** `farm/control/**`, `farm/executors/**`, `farm/ai/**`, `farm/gateway/**`, `farm/mcp/**`, earlier migrations,
`config/*`, `console/`, `pyproject.toml`, any other file.

**Done when:** `check.ps1` ends with `RESULT: all passed` (quarantined tests run separately and do not block; yours must no
longer be in the list); every finding above has a regression test that fails on the old code.

**Reply (15 lines or fewer):** per finding: fixed + test name · migration summary · `check.ps1` tail · open issues.
