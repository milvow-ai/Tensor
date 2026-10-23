# Q0F — Fix the MCP relay, OAuth and Console-number findings (quality baseline)

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` file or token store.
Never run a migration against a live database. Run every command in the foreground and wait for it (~20 minutes for the check).

**Goal.** Close the audit findings in the MCP path and the dashboard numbers. Q0-03 is the same defect class as D1 (account
state decided from words in a result), so apply the same principle: structured signals first, error channels only, exact
signatures, unknown stays unknown.

**Read first:** `briefs/CONTEXT.md` · `docs/QUALITY.md` §2 (the classification principle) · `docs/audits/Q0-audit.md` (rows
below, in full) · reproductions in `D:/dev-cache/runs/q0c/repro/test_q0c_repro.py` · `farm/executors/mcp/*`.

**Findings to fix:**
- **Q0-03** the typed-capability MCP path classifies a tool's own `isError` text and exception text by bare substrings (`401`,
  `403`, `429`, `404`, `exhausted`). A tool's own error is a **result**, never an account state. Only the transport path
  (HTTP status from the status tap, JSON-RPC error codes, OAuth failures) may change account health, using exact signals.
  Test: "Company 4031 not found" returned with `isError` never flips the account.
- **Q0-11** OAuth refresh must be serialised across processes, not only inside one (a lock that works across processes on the
  token store — file lock or a database advisory lock), and a lost refresh race must re-read the winner's token instead of
  clearing the store. Test with two processes refreshing at once.
- **Q0-25** the HTTP status tap must be per call, not one shared slot per connection, so concurrent calls cannot erase or steal
  each other's 401 / 402 / 429 evidence.
- **Q0-09** dashboard counts: `calls_today`, `v_cost_per_result.successful_results` and `v_idle_paid` must not count cache hits
  and single-flight joins as calls of the account that originally answered. Fix the SQL; test the views against hand-built rows.

**Database changes** go in one new migration `farm/db/migrations/versions/0017_q0_mcp_console_fixes.py` with
`down_revision = "0016"` (raw SQL; recreate changed views there).

**Owns:** `farm/executors/mcp/{client,connect,relay,mapping}.py` · `farm/executors/mcp/__init__.py` · the new migration 0017 ·
`tests/test_q0f_*.py` (new) · existing tests that asserted the old, wrong behaviour (list each).

**Must not touch:** `farm/executors/cli_agent/**`, `farm/resources/**`, `farm/control/**`, `farm/gateway/**`, `farm/mcp/**`,
earlier migrations, `config/*`, `console/` source files, `pyproject.toml`, any other file.

**Done when:** `check.ps1` ends with `RESULT: all passed`; every finding above has a regression test that fails on the old code.

**Reply (15 lines or fewer):** per finding: fixed + test name · migration summary · `check.ps1` tail · open issues.
