# Q0G — Quality follow-ups from Q0D (D39–D42)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. Never run a
migration against a live database. Run every command in the foreground; run ruff, mypy and the relevant test files yourself
(the gate runs the full suite after you).

**Goal.** Close the four follow-ups Q0D left open so the dashboard tells the truth and there is one budget rule.

**Read first:** `briefs/CONTEXT.md` · `docs/QUALITY.md` D39–D42 · `docs/audits/Q0-audit.md` (R1b, R3b, B1) · the Q0D migration
`0015_q0_router_fixes.py` · `farm/resources/{health,ledger,budget}.py` · `farm/manager/budgets.py` · the Console views that show
connection status and quota meters (find them in the migrations: `v_connection_status`, the meter view).

**Fix:**
- **D39** `v_connection_status` must show an account whose circuit is open or half-open (probe lease held) as not active —
  `limited` or `probing` with its reason — never `active`.
- **D40** one budget check path: `check_paid_call` in `farm/manager/budgets.py` uses the same reservation-aware rule as the
  router (settled + reserved spend), or delegates to it. Test: concurrent calls through both paths cannot overshoot a hard stop.
- **D41** when an executor reports more usage than its estimate and `used + reserved` passes the limit: record the real usage
  (never drop it), mark the window exhausted, and block the next call on that account until the window frees. Test it.
- **D42** meters for `rolling_5h` units show the sliding 5-hour sum (`farm_quota_consumed`), not the lifetime counter; a past
  `next_reset_at` is cleared when its window resets.

**Database changes:** one migration `farm/db/migrations/versions/0018_q0g_followups.py` with `down_revision = "0017"`, full
downgrade. Recreate a view only if its definition changes.

**Owns:** `farm/manager/budgets.py` · `farm/resources/ledger.py` · `farm/resources/budget.py` · the new migration 0018 ·
`tests/test_q0g_*.py` (new) · existing tests that asserted the old behaviour (list each).

**Must not touch:** `farm/db/pool.py`, `farm/gateway/**`, `farm/control/**`, earlier migrations, `config/*`, `console/`, any
other file.

**Done when:** ruff + mypy clean; your tests pass and fail on the old code (say how you showed it); the gate passes after you.

**Reply (10 lines or fewer):** per item: fixed + test name · migration summary · open issues.
