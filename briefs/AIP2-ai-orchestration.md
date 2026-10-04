# Brief AIP2 — AI orchestration through the Farm: jobs, fan-out, iteration, exact results

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`. Run commands in the foreground and report once.

## Goal (owner, 2026-10-05)
The main AI (Claude Code, Codex, …) uses the Farm to **run other AIs as workers**: "give this task to 3 Claude accounts and 2 Gemini", check on them, read each **exact** result, find what is wrong, send a follow-up to **the same worker** (same account, same session), repeat. Each worker is a CLI on its own account (Claude per `CLAUDE_CONFIG_DIR` on different emails, Codex per `CODEX_HOME`, Gemini via agy, Hermes profiles). All communication goes through the Farm; the Farm stores the trajectory (account, model, tokens, cost, status, native session id, turn count), never transcripts. M3e-B already has blocking `ask_ai` / `ask_ai_batch` / `list_ais` with sticky sessions — keep them working; this adds the non-blocking layer on top.

## Read first
`briefs/CONTEXT.md`, `briefs/M3e-ai-pool.md`, `farm/executors/cli_agent/*` (SEC1 rules: env allow-list, id validation, edit confinement — do not weaken), `farm/capabilities/schemas.py` (`AskAiIn/Out`), `farm/gateway/server.py` (ask_ai tools), `farm/resources/router.py` public API.

## Owns
`farm/ai/` (new package: `jobs.py`, `conversations.py`), `farm/gateway/ai_tools.py` (new; move the M3e-B ask_ai tool registrations here; `server.py` keeps one registration call), `farm/capabilities/schemas.py` (AI section only), `farm/executors/cli_agent/codex.py` + `agy.py` (multi-account home dirs only), `farm/control/cli.py` (`farm ai jobs|show|cancel`), one new Alembic migration (next free number), tests `tests/test_ai_jobs.py`, `tests/test_ai_conversations.py`, `tests/test_accept_aip2.py`.

## Build
1. **Jobs (non-blocking).** Tools: `ai_start(task, ai="any", account?, model?, mode="answer"|"edit", cwd?, conversation_id?, json_schema?, timeout_s≤7200)` → `{job_id, conversation_id, account}` immediately · `ai_start_many(jobs: [...], distinct_accounts=true)` (e.g. 3×claude + 2×gemini; with `distinct_accounts` each job gets a different account of that AI, error if not enough usable accounts — say which) · `ai_status(job_ids?)` (state queued|running|succeeded|failed|cancelled, account, elapsed, tokens so far if known) · `ai_wait(job_ids, mode="any"|"all", timeout_s≤300)` (returns finished ones; caller loops) · `ai_result(job_id)` · `ai_cancel(job_id)` (tree-kill, state cancelled, reservation released). Jobs live in a new table `ai_jobs` and run inside the Farm process (thread runner per SEC1/FIX1 rules), so they continue when the calling MCP client disconnects; on Farm restart, running jobs are marked failed with kind `farm_restart` (no silent re-run). Concurrency limit per account (`meta.max_parallel`, default 1) and per AI pool; extra jobs queue.
2. **Conversations (iteration).** `ai_reply(conversation_id, message, timeout_s?)` → new job on **the same account and native session** (claude `--resume`, codex `exec resume`, agy `--conversation`, hermes `--resume`). Table `ai_conversations(id, ai, account, native_session_id, turns, tokens, cost, last_job_id, created_at, updated_at)`. If that account is now limited/logged out → fail with kind `account_unavailable` and `retry_at` (never silently move a conversation to another account — the session lives there). `ai_conversations(list)` tool for the caller to see open threads.
3. **Exact results + clear errors.** `ai_result` returns the worker's final text unmodified, parsed JSON when `json_schema` was given (validation errors reported, raw text kept), native session id, model, usage, cost, duration, files changed (edit mode: `git status --short` of `cwd` when it is a repo). Results over `settings.ai_result_inline_chars` (default 200 000) are written to `FARM_DATA_DIR/ai-results/<job_id>.txt` and returned as path + first 20 000 chars. Failures return `{kind: limit|auth|timeout|crash|bad_request|cancelled|farm_restart|account_unavailable, ai, account, message, retry_at?}` so the caller knows exactly which worker failed and why. Optional `retry_other_account=true` on `ai_start` (answer mode only, first turn only) reruns a `limit|auth|crash` failure on the next account and records both attempts.
4. **Accounts.** `list_ais` shows per account: status, login state, limit/reset, active + queued jobs, max_parallel. Codex: one `CODEX_HOME` per connection (`meta.home`). Agy: check locally (`agy --help`, docs in `library/` if present) whether a per-account config/home dir is possible; implement it if yes, else keep one account and record the limitation in the reply. Do not install new CLIs.
5. Budget/quota: every job reserves and commits through the router/ledger like `ask_ai` (cost from the CLI's usage when reported, else estimated + flagged).

## Acceptance tests (fake-CLI harness only, no real CLIs, no network)
- `ai_start_many` with 3 claude + 2 gemini fakes, `distinct_accounts` → 5 jobs on 5 different accounts, all run in parallel up to max_parallel; `ai_wait(all)` returns 5 exact results (byte-identical to the fake output).
- `ai_reply` lands on the same account with the native session id passed through; turn count 2.
- Account limited mid-conversation → `account_unavailable` with `retry_at`; first-turn job with `retry_other_account` → succeeds on another account, both attempts in the run row.
- `ai_cancel` kills the process tree and releases the reservation; MCP client disconnect does not stop a running job; restart marks running jobs `farm_restart`.
- Large output → file path + preview. Blocking `ask_ai`/`ask_ai_batch` tests from M3e-B still pass unchanged.
- SEC1 tests still pass (env allow-list, id validation incl. conversation/job ids, edit confinement).

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 15 lines: files, test count, check tail, agy multi-account finding, deviations.
