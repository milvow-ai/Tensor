# Recent changes

Newest first. One entry per working session or merged round: what changed, why, and what is not done. Older notes from other
tools live in `updates/`. The plan and its status live in `.orchestrate/state.md`.

---

## 2026-10-24 (later) — UI kit approved; second session loss; PC memory leak found

- **Owner approved the dashboard look (Round 0)**: "Apply your 6 polish fixes and merge the UI kit." Gemini applied the six
  fixes (A3A-R1: numbers in Geist Sans with tabular figures, current models and account ids in the sample data, sidebar scrolls
  with the plan card pinned, day-chip label fits, plain gallery copy, locale toggle on "Tokens consumed"); vitest 49/49. Not
  merged yet: its end-to-end tests and screenshots could not run because the PC ran out of memory.
- **The Claude Code session ended a second time.** The builders survived this time (F1B, S0, A3A kept running); only the
  notifications were lost.
- **The live Farm died at 10:19:31 with no log line** and was restarted detached. Cause found afterwards (D47): Windows' commit
  memory was exhausted (47 of 50 GB while all processes together hold 18 GB). Three vendor services leak handles —
  AcerSystemCentralService (374k), AcerCentralService (284k), MediaTek Bluetooth `mtkbtsvc` (229k) — and the console host the
  Farm was attached to crashed when it could not grow its stack. Fix on the PC: restart those services or reboot. Fix in the
  product: run the Farm without a console (D47). Until then heavy jobs run one at a time.
- **Drive C: is full** (0.2 GB free of 280 GB; D: has 52 GB free). With no room on C: Windows cannot grow the page file,
  which is why memory ran out. `scripts/round.ps1` now puts temp files on D: (`D:\dev-cache\tmp`) and saves the full gate
  output to `D:\dev-cache\runs\<name>\gate-full.txt`. Owner: "move to D drive". Moved, nothing deleted: the stale npm cache
  (6.7 GB) and uv cache (1.6 GB) to `D:\dev-cache\from-c\` (npm, uv and Playwright already read from D: through user
  variables), and the Playwright browsers that existed only on C: into `D:\dev-cache\ms-playwright` (duplicates to
  `from-c`), then 107 unused temporary browser profiles `Temp\cdp-*` (7.6 GB, 21–23 Oct) to
  `D:\dev-cache\from-c\temp-browser-profiles`. **C: free 0.5 → 16.9 GB; D: free 34.7 GB.** `D:\dev-cache\from-c`
  (≈ 17 GB) is safe to delete later. Still on C: for the owner to decide: Downloads 18.6 GB, Chrome data 9.5 GB, old temp
  folders `DiagOutputDir` (2.8 GB) and `p0q1fb44` (2.1 GB), page file 34 GB (inflated by the handle leak; a restart shrinks
  it). Google Play Games claims 512 GB but its disk image is sparse (0.2 GB real).
- **F1B gate, first try: 118 failed / 2,330 passed** in 29 min, while A3A and S0 ran in parallel and C: was full. A failed
  test passes alone, so the gate was re-run on its own with temp files on D:.
- **S0 merged** (`6bb45ea`, docs only + `spikes/acp_client.py`): Claude Code takes a per-run Farm MCP with a tool allow-list
  (the allowed call ran, a forbidden one was denied) and runs as a warm ACP session (follow-up turn 1.4 s against ~17 s
  one-shot); Gemini and Codex ACP blocked at login; Hermes connects through a per-run `HERMES_HOME`, its live call needs the
  provider key in the run's environment; metering fields per runtime recorded. ADR 14 input in `docs/spikes/README.md`.
- `briefs/CONTEXT.md` §6: builders must never stop, restart or kill a process they did not start (above all the live Farm).
- `scripts/round.ps1`: `-Foreground` (waits in-process so the lead is notified), `-Main` (defaults to the current branch),
  `-ClaudeConfigDir`; Hermes rounds use GLM-5 through the Bedrock mantle endpoint (owner's direction).

---

## 2026-10-24 (later) — Session ended mid-build: recovered; live database at 0018

- The previous Claude Code session ended; the live Farm and two builders (S0, F1B) were child processes and stopped with it.
  No work was lost: F1B's changes were on disk and its conversation was resumed by session id.
- **Live Farm restarted detached** from any session (WMI-created process running `D:\farm-data\bin\start-farm.cmd`), health OK.
- **Live database migrated 0017 → 0018** (Q0G) while the Farm was down: logical backup first
  (`D:\farm-data\backups\logical-20261024T043513Z`, 35 tables, 2,325 rows).
- **Live role check for F1B:** the Supabase `postgres` role is not a superuser but has `BYPASSRLS` and `CREATEROLE`, so the
  `farm_system` maintenance role can be created there.
- **Q0G merged** (`369537e`) — probing/open circuits never shown as active, one budget rule, overruns recorded and blocking,
  rolling 5-hour meters.
- **F1B status:** built (roles, RLS, fail-closed tenant checkouts, `api_tokens`, isolation suite with 105 tests over 30 tables,
  17 tools, 18 command kinds, 13 views). Finishing: re-chain its migration after 0018 and move the ledger and budget checks onto
  tenant / system connections.
- **New register entries:** D45 global text primary keys block a second tenant (F1C, must precede onboarding); D46 session-bound
  processes.
- `scripts/round.ps1`: the Claude builder's input/output now go through files (a PowerShell pipeline crashed with a
  StackOverflowException in round f1b).

---

## 2026-10-24 (later) — Quality baseline complete: all 25 audit findings fixed; live database at 0017

**Merged (each behind the full gate: ruff, mypy, all tests, secret scan):**
- **Q0D** (`d8cc04b`) — routing core: the critical stuck half-open account (Q0-01) now ends every probe in a defined state;
  spend is reserved before paid calls (five concurrent $0.50 calls against $1.00 → 2 pass, 3 blocked); failover follows
  priority; `most_remaining` uses real quota; true rolling 5-hour window; late commits recorded; reservation leak closed; cache
  only for `found = true`; scheduler honours cooldowns; no pool deadlock on login failures. Migration 0015.
- **Q0E** (`17fd09a`) — owner control: commands claimed exactly once (old code: 372 executions for 200 commands); rejected
  commands change nothing; stuck commands and runs are swept; alerts once per reference; one month-end forecast formula; correct
  renewal dates. **The "always failing" shutdown test was a product bug**: the Farm killed AI workers before marking their jobs
  interrupted — fixed. Migration 0016.
- **Q0F** (`42c049d`) — a tool's own error never changes account health (transport signals only); HTTP status recorded per
  call; OAuth refresh locked across processes (Windows and Linux) and a lost race reuses the winner's token; dashboard counts
  exclude cache hits. Migration 0017.

**Live database migrated 0014 → 0017:** logical backup first (`D:\farm-data\backups\logical-20261024T031003Z`, 34 tables,
2,304 rows) → stop → `farm db migrate` → start (PID 611428). Verified: revision 0017, `budget_reservations` present, no rows
lost, no runs stuck in `running`; `farm ai test agy-01` and `claude-04` → PONG.

**Quarantine:** one test left — `test_accept_aip2 … kills_the_worker` (D38, ~1 in 20 under load after the product fix; owner F2).

**Registered follow-ups:** D37 Windows pipe-reader crash (check the CLI runner), D39–D42 (Q0G), D43 watch.

**Started:** Q0G (follow-ups D39–D42, migration 0018) and F1B (enforced tenant isolation: `farm_app` / `farm_system` roles,
fail-closed tenant checkout, session-default `tenant_id`, RLS policies, `security_invoker` views, `api_tokens` replacing
`client_tokens`, dynamic isolation suite; migration 0019).

---

## 2026-10-24 (later) — Tenancy schema merged and the live database migrated

- **F1A merged** (`b442e94`, full gate green): `tenants`, `users`, `memberships`; `workspace_id` renamed to `tenant_id` on every
  tenant-owned table, with FKs and tenant-first indexes; Milvow is tenant #1 (`00000000-0000-0000-0000-000000000001`).
  Expand step only: defaults stay until F1B passes the tenant explicitly and enforces RLS.
- **Live Supabase database migrated 0013 → 0014** (Postgres 17.11):
  1. Logical backup first (the local `pg_dump` is v16 and cannot dump a v17 server): every public table to CSV with row counts
     and SHA-256 — `D:\farm-data\backups\logical-20261024T004114Z` (31 tables, 2,260 rows, revision 0013).
  2. Farm stopped (whole process tree) → `farm db migrate` → Farm started (PID 488016) → health OK.
  3. Verified: revision 0014; tenant `milvow` present; every table has `tenant_id` except the platform tables and
     `client_tokens` (D34); row counts unchanged except `mcp_tools` 189 → 192 (the tool-catalog refresh at start-up).
  4. Live: `farm ai test agy-01` → PONG; `farm ai test claude-02` → PONG; `clay__get-credits-available` answers.
- **Started:** fix rounds Q0D (routing core), Q0E (commands, alerts, runs), Q0F (MCP relay, OAuth, Console numbers) on
  Claude account 2, migrations 0015 → 0016 → 0017.

---

## 2026-10-24 (later) — Quality baseline: first merges, verified live

**Merged into `effort2/stage1-saas` (each behind the full gate: ruff, mypy, all tests, secret scan):**
- **Q0C** (`0b37430`) — read-only defect audit: 25 findings (1 critical, 10 major, 14 minor; 17 confirmed with reproductions)
  in `docs/audits/Q0-audit.md`. Docs only.
- **Q0B** (`df46565`) — one account-state classifier for all five AI CLIs: structured result first, error channels only, exact
  signatures from real recorded outputs. The old code scanned the AI's own answer for words like "403" or "capacity" and
  falsely flagged accounts. Proof: the old code fails 3 of 6 real-output replay tests; generated tests cover all five CLIs.
- **F3P** (`merge f3p`) — Linux operations scripts and systemd units by Hermes GLM-5, revised after review (7 defects), then two
  more secret-scan defects fixed by the lead (fake key literal; silent pass when git cannot list files). Proven: loud failure
  without git; 626 files scanned on a real clone.
- **Q0A** (`eebf230`) — "Unknown tool" root cause: FastMCP's catalog filter hid tools marked UI-only
  (`_meta.ui.visibility` without "model"); loose names that matched several tools now error instead of picking one; compact
  `search_tools` (≈2.8k characters for 10 hits instead of 161k for 25) + `describe_tool`.

**Gate:** `tests/quarantine.txt` holds four registered baseline test failures (D7). They still run and print, but do not block;
owners Q0D / Q0E. Base lint error (agy.py) fixed.

**Verified on the live Farm (restarted on the new code, PID 476392):**
- agy-01 (Gemini Pro) had a stale false "needs login" status from the old code; resumed through an audited `resume` command;
  `farm ai test agy-01` → PONG.
- `call_tool("clay__get-credits-available")` → answers (`hasWorkspaceCredits: true`).

**New register entries:** D34 `client_tokens` created outside migrations (→ F1B); D35 silent-pass secret scan (fixed); D36 tool
risk labels trust server hints — `clay__search-contacts-by-name` shows read-only but can spend credits (→ A2 overrides).

**Waiting:** UI kit (A3A) Round 0 — owner's review of the gallery screenshots. F1A (tenancy schema) merge next, then a backed-up
live database migration.

---

## 2026-10-24 — Effort 2 starts: SaaS foundation → Stage A → Tensor

**Branch:** `effort2/stage1-saas`, created from `claude/awesome-archimedes-vb1t7y` (3db3d81, which still had 8 unpushed commits).

**Design written (binding for every brief):**
- `docs/ARCHITECTURE.md` — multi-tenant SaaS platform: tenancy and two-layer isolation, process roles, queue and leases,
  sandboxes, vault, storage, cells, technology stack, engineering standards, 21 decisions (ADRs).
- `docs/AGENTS.md` — the Farm as the operating system for AI work: what an agent needs and what the Farm provides, delegation
  modes, runtimes, agent selection, token-efficiency levers, memory, cost accounting, the operator-permission fix.
- `docs/PRODUCT.md` v2 — the dashboard from the user's jobs; patterns learned from OpenRouter, Trigger.dev, Langfuse, Obot,
  MCPJungle, Linear and Vercel.
- `docs/design/DASHBOARD-COMPONENTS.md` + `docs/design/reference-dashboard.png` — the owner's visual reference turned into tokens,
  components and page layouts.
- `docs/QUALITY.md` — defect register and the quality baseline (Q0) that comes before any new building.
- `briefs/CONTEXT.md` — builder context now says multi-tenant SaaS.

**Code and tooling:**
- `farm/control/commands.py` — `test_connection` timeout 10 s → 120 s (Codex through WSL starts slowly). Found uncommitted from
  the 2026-10-15 session; committed as is.
- `scripts/round.ps1` — can drive Claude pool accounts (`-Builder claude`, default account claude-02, Sonnet), follows the
  current branch (`-Main`), and can run in the foreground (`-Foreground`) so the operator is notified when a round ends.

**Repository hygiene:**
- `config/registry.yaml` had the owner's live account list (13 email addresses) written over the generic seed file. It is kept
  out of commits with `git update-index --skip-worktree`; the owner's live registry belongs in the git-ignored
  `config/registry.owner.yaml`. `config/*.bak-*` is now ignored.

**Findings:**
- Builder check: Claude account 2 on Sonnet 5.5 works; Antigravity (Gemini Pro account) works even though the Farm lists it as
  "needs login" — confirms defect D1. Hermes GLM-5 works through the **default profile with `--provider custom:bedrock-mantle`**
  (owner's correction); only the old `farm-builder-bedrock` profile had an expired token. `scripts/run-hermes.ps1` and
  `scripts/round.ps1` now use that route for GLM-5 by default.
- D1 root cause (in code): the CLI drivers scan the AI's own answer for error words before checking structured success.

**Wave 1 (running):** Q0A tool lookup and compact search · Q0B account-state classifier · Q0C read-only defect audit · F1A
tenancy schema — all Claude account 2 (Sonnet); A3A dashboard UI kit — Antigravity (Gemini 3.8); F3P Linux operations
scripts and systemd units — Hermes GLM-5.

**Not done yet:** everything after wave 1 (see the plan); the live database has not been migrated; no deploy.
