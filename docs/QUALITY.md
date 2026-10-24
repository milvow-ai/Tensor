# Harness Farm — quality baseline (Q0) and how defects are kept out

Owner rule (2026-10-24): **no new building on top of a known defect.** Constant bug fixing burns tokens and trust; the cure is
fixing **classes** of defects at their root, proving each fix with a test built from real outputs, and keeping them out with
gates — not patching symptoms one at a time.

## 1. Defect register (open)

| # | Defect | Evidence | Class | Root cause / hypothesis |
|---|---|---|---|---|
| D1 | **AI accounts falsely flagged** "needs login" or "limit reached" after successful runs | Code: `farm/executors/cli_agent/agy.py` 147–199 and `claude.py` 101–156 run auth/limit regexes over **stdout + stderr — including the AI's own answer — before checking the structured success status**. Patterns are broad (`capacity`, `/login`, `rate limit`, `\b403\b`, `resets in`). Live 2026-10-23: agy-01 `needs_login` with a `"status":"SUCCESS"` last error; claude-02 / -04 last errors are success JSON | **State classification** | Verified for agy and claude; codex and hermes drivers to check (same structure suspected) |
| D2 | `call_tool("clay__get-credits-available")` → "Unknown tool" although the tool is registered | Live 2026-10-24 | Tool naming / discovery | Hypothesis: hyphen / underscore name-variant resolution |
| D3 | `search_tools` returns full schemas: 161,435 characters for 25 tools | Live 2026-10-24 | Context economy | Search returns `list_tools`-format definitions |
| D4 | codex-01 marked `needs_login` with the error "failed to record rollout items: thread … not found" | Live `list_ais` 2026-10-23 | State classification | Probably a Codex session error misread as an auth failure (same class as D1) — verify |
| D5 | `farm run` does not start again after a reboot; one MCP connect timed out (30 s) while the process was alive | 2026-10-07 state notes; 2026-10-23 session | Availability | No service install; hang cause not yet diagnosed |
| D6 | 8 unpushed commits; uncommitted `config/registry.yaml` (+910 lines) and `farm/control/commands.py`; ~25 stale worktrees (~20 GB) | git, disk scan 2026-10-23 | Repository hygiene | Pushed and committed 2026-10-24 (registry.yaml kept local: it holds account emails); worktree cleanup pending the owner's OK |
| D7 | Baseline test failures: `test_accept_aip2 … kills_the_worker` fails **every run, even alone**; `test_accept_run1 … watchdog_restart_and_alert_on_failure`, `test_accept_m2_bulkhead … within_max_queue_wait` and `test_adapter_zerobounce … [json-429-rate_limited]` fail only under parallel load (the last passes 136/136 alone) | Gates of rounds q0c (docs-only), f3p (new files only) and q0b, 2026-10-24 | Test reliability | Quarantined in `tests/quarantine.txt` (still run and printed by `check.ps1`, not blocking). Owners: Q0E (aip2, run1), Q0D (bulkhead, zerobounce). Fix by making them deterministic (controlled clock / events), never by raising timeouts; each owner deletes its line |
| D8 | Lint (ruff E501) failed on the base branch: `farm/executors/cli_agent/agy.py:92` | Gate of round f1a, 2026-10-24 | Gate hygiene | Fixed 2026-10-24 (comment wrapped) |
| D34 | `public.client_tokens` (MCP client tokens) is created at runtime by `farm/control/run.py`, outside any migration: no `tenant_id`, no RLS, no migration history | Found by F1A, 2026-10-24 | Schema hygiene / tenancy | F1B: move it into a migration with `tenant_id`, hashed tokens, RLS; remove the runtime `create table` |
| D35 | The secret scan in the Linux `check.sh` could pass with nothing scanned when `git ls-files` failed (`|| true`), and its self-test key literal tripped the repo scan | Lead verification of F3P, 2026-10-24 | Silent-pass checks | Fixed 2026-10-24: loud failure without git; key built at runtime; proven (626 files scanned on a real clone) |
| D36 | Tool risk labels come only from the MCP server's hints: `clay__search-contacts-by-name` is labelled read-only but enriches contacts and can spend credits | Live `search_tools`, 2026-10-24 | Approval safety | A2: Farm-side risk overrides per tool (default \"may-spend\" for enrichment / write-like tools), editable in the dashboard |
| D37 | A Windows fatal exception in a subprocess pipe-reader thread crashed one test run (test_accept_run1); the Farm's own CLI runner also reads child pipes in threads | Q0E report, 2026-10-24 | Process robustness (Windows) | Check `farm/executors/cli_agent/base.py` pipe readers under load; prefer file-backed or asyncio-safe readers; add a stress test |
| D38 | `test_accept_aip2 … kills_the_worker` still fails 1 in 20 under load after the shutdown-order product bug was fixed (Farm closes during MCP start-up, "Connection closed", cause unknown) | Q0E load proof, 2026-10-24 | Test reliability | Stays quarantined; owner F2 (process roles / start-up) |
| D39 | `v_connection_status` (Console) still shows an account stuck in half-open as `active` (audit R1b) | Q0D report, 2026-10-24 | Dashboard truth | Q0G: view reads the probe lease / circuit state |
| D40 | `farm/manager/budgets.py check_paid_call` still reads settled spend only; the router path now reserves spend (Q0-04) | Q0D report | Budget safety | Q0G: one budget check path, reservation-aware |
| D41 | `used + reserved` can exceed a limit when an executor reports more usage than its estimate (audit R3b) | Q0D report | Quota correctness | Q0G: decide policy (record overrun + block next call), test it |
| D42 | `quota_usage.used` for `rolling_5h` stays a lifetime counter for display, so the Console meter never decays; a past `next_reset_at` is ignored only at candidate load | Q0D report | Dashboard truth | Q0G: display from the rolling sum |
| D43 | `test_accept_open1 … never_a_row_a_log_or_the_registry` failed once with "Unknown tool" from stdio sync in a full run; passes alone | Q0D report | Test reliability | Watch; quarantine if it repeats |
| D44 | `console/sql/views.sql` still holds pre-0018 view text; the migrations are the source of truth | Q0G report, 2026-10-24 | Doc drift | A3: generate the reference file from the database, or delete it |
| D9–D33 | 25 findings of the read-only audit Q0C (1 critical, 10 major, 14 minor; 17 confirmed with reproductions) | `docs/audits/Q0-audit.md`; reproductions in `D:/dev-cache/runs/q0c/repro/` | Several | Fix rounds Q0D+ |

## 2. The fix for the state-classification class (D1, D4)

1. **Structured result first.** Success or failure comes from each CLI's structured output (Claude `is_error` / `subtype`;
   Codex event stream; agy `status`; Hermes usage file `failed`) and the exit code — never from words in the answer.
2. **Error channels only.** When the run failed, the error kind is read from stderr and the structured error field only. The
   model's answer text is never scanned for error words.
3. **Precise signatures from real outputs.** Each CLI gets a table of exact error signatures taken from recorded real outputs
   (login expired, limit reached with reset time, rate limited, overloaded), not broad words.
4. **Unknown is unknown.** An unrecognised failure is `unknown`: it counts against health but never flips an account to
   "needs login" or "exhausted".
5. **Confirm before flipping state.** "Needs login" and "exhausted" are set only on a strong signature **and** a cheap
   confirmation probe; each state change stores its evidence (excerpt, source run, time), shown in the dashboard with
   **Re-check now** (`docs/PRODUCT.md` §2.2).
6. **Proof:** golden fixtures of real success and failure outputs per CLI; a property test that a success output containing
   every trigger word still classifies as success; one classifier module shared by all drivers.

## 3. How Q0 runs

1. **Register** every known defect here with evidence.
2. **Audit by area**, read-only, with a fixed checklist and a reproduction for each finding: state classification (all CLI and
   MCP drivers), router and ledger invariants, MCP relay and OAuth refresh, AI jobs and leases, Console numbers vs SQL.
3. **Fix by class**, each with a regression test that fails on the old code and passes on the new.
4. **Soak:** the team uses the Farm for 48 hours. Exit needs zero false account flags, zero crashes, zero unexplained
   restarts, and every account state on the dashboard matching a manual check.

## 4. Keeping defects out (from Q0 on)

- **Recorded real outputs** for every external interface (CLIs, MCP servers, APIs) as test fixtures; refreshed when a provider
  changes format.
- **Property-based tests** (Hypothesis) for every parser and classifier.
- **Invariant tests** for money and quota: used + reserved never exceeds a limit; no reservation outlives its TTL; no job runs
  twice.
- **Gates:** nothing merges without the full suite, the isolation suite (from F1) and a fresh check worker's pass.
- **Every production defect** gets a register entry, a root-cause line and a test before it is closed.
