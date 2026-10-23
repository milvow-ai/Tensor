# Q0E — Fix the command, alert, renewal, run-sweep and AI-job findings (quality baseline)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file. Never run a
migration against a live database. Run every command in the foreground and wait for it (the full check takes ~20 minutes).

**Goal.** Close the audit findings in the owner-control and bookkeeping paths. Fix each at its root; turn each reproduction into
a permanent regression test that fails on the old code and passes on the new.

**Read first:** `briefs/CONTEXT.md` · `docs/audits/Q0-audit.md` (the rows named below, in full) · the reproductions in
`D:/dev-cache/runs/q0c/repro/test_q0c_repro.py` · `docs/QUALITY.md` D7 · the code each finding cites.

**Findings to fix:**
- **Q0-02** a command can run twice: claim each command atomically (`update … set status='running', claimed_by, claimed_at
  … where id = … and status = 'queued' returning …`, or `FOR UPDATE SKIP LOCKED` inside one transaction). A `done` command
  can never run again. Test with two consumers racing over 200 commands.
- **Q0-10** a rejected command must change nothing: validate fully before writing; run each command's writes and its audit row
  in one transaction.
- **Q0-17** a command whose process dies while `running` must not stay `running` for ever: a claim lease (`claimed_at` +
  heartbeat or timeout) and a sweep that fails expired claims with a clear reason.
- **Q0-20** a run whose process dies must not stay `running` for ever: a sweep that marks orphaned runs `failed` with
  `error_kind = 'interrupted'` after a lease period.
- **Q0-06** threshold alerts are sent once per month per threshold even after the owner acknowledges them.
- **Q0-15** one month-end forecast formula, shared by the alerts code and the Console view (fractional days elapsed); test both
  give the same number on 15 Oct 12:00.
- **Q0-16** one renewal-date rule for `billing_day` 29–31 in both the view and the reminder job (clamp to the month's last day,
  never roll into the next month); test Feb, Mar and 30-day months.
- **D7 (your two quarantined tests)** `test_accept_aip2::test_stopping_farm_serve_while_a_job_runs_fails_it_as_farm_restart_and_kills_the_worker`
  fails **every run, even alone** — find out whether the test or the shutdown behaviour is wrong, and fix the real cause (if a
  job is not failed as `farm_restart` on a graceful stop, that is a product bug). `test_accept_run1::test_watchdog_restart_and_alert_on_failure`
  fails under load — make it deterministic (events, never a bigger timeout). Then delete both lines from
  `tests/quarantine.txt`; prove each by running it 20 times in a row while the full suite runs in parallel.
- Q0-18 and Q0-19 (AI-job lease fencing, per-process limits) are **not** in this round — the F2 lease table fixes them.

**Database changes** go in one new migration `farm/db/migrations/versions/0016_q0_control_fixes.py` with
`down_revision = "0015"` (raw SQL). If a Console view changes (Q0-15, Q0-16), recreate it in this migration.

**Owns:** `farm/control/{commands,alerts,heartbeat}.py` · `farm/manager/{alerts,budgets,renewals}.py` ·
`farm/resources/trajectory.py` · `farm/ai/jobs.py` and `farm/control/run.py` (only what the D7 fix needs) · the new
migration 0016 · `tests/test_q0e_*.py` (new) · `tests/test_accept_aip2.py` · `tests/test_accept_run1.py` · your two lines in
`tests/quarantine.txt` · existing tests that asserted the old, wrong behaviour (list each).

**Must not touch:** `farm/resources/` other than `trajectory.py`, `farm/manager/scheduler.py`, `farm/executors/**`,
`farm/gateway/**`, `farm/mcp/**`, earlier migrations, `config/*`, `console/` source files, `pyproject.toml`, any other file.

**Done when:** `check.ps1` ends with `RESULT: all passed`; every finding above has a regression test that fails on the old code.

**Reply (15 lines or fewer):** per finding: fixed + test name · migration summary · `check.ps1` tail · open issues.
