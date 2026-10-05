# Brief AIP4 — free capacity pool: Gemini CLI accounts, free-first routing, RAM-safe concurrency

Rules: follow `briefs/CONTEXT.md` §6 (incl. "Reuse first"). Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.
Run every command in the foreground and wait for it; never start one in the background and poll it. Report once.

## Goal (owner, 2026-10-05)
Use every AI account the owner has — paid and free — as workers the calling AI delegates to through the Farm, on ONE Windows PC (15 GB RAM),
without paying for more plans. Official programmatic doors only:
- **Gemini**: Google's official Gemini CLI (`@google/gemini-cli`), one Google login per account. Free personal accounts: 60 req/min, 1,000 req/day,
  Flash models (Pro models need a paid plan since 2026-03-25). Paid Google AI Pro accounts get Pro models.
- **ChatGPT**: Codex CLI (`codex exec`), one ChatGPT login per account; every ChatGPT plan (Free, Go, Plus, Pro) includes Codex with plan limits.
- **Claude**: Claude Code with a Pro/Max login (free Claude plans cannot use Claude Code).
- **Free models**: Hermes via OpenRouter free models (already working).
Default policy: **free first** — route to free accounts while they have quota, fall back to paid, and tell the caller which account answered.

## Read first
`briefs/CONTEXT.md`, `farm/executors/cli_agent/*` (SEC1 rules: env allow-list, id validation, no shell, edit confinement), `farm/ai/jobs.py`
(per-account `max_parallel`, queue), `farm/resources/{router,ranking,strategies}.py`, `farm/control/cli.py` (`farm ai login|test`), Gemini CLI
`--help` output once installed (`gemini --help`), and its docs in `node_modules/@google/gemini-cli` if present.

## Owns
`farm/executors/cli_agent/gemini_cli.py` (new driver) + registration in `farm/executors/cli_agent/__init__.py`, `farm/executors/cli_agent/base.py`
(only: shared global concurrency gate), `farm/ai/jobs.py` (global cap + free-first ordering hook), `farm/resources/ranking.py` (plan-tier signal),
`farm/registry/models.py` (connection meta: `plan: free|paid`, `daily_requests`), `farm/control/cli.py` (`farm ai login|test` for gemini-cli),
tests `tests/test_cli_agent_gemini_cli.py`, `tests/test_ai_free_first.py`, `tests/test_ai_global_cap.py`.

## Build
1. **Gemini CLI driver** (`cli: gemini`): one-shot `gemini -p` with the prompt on stdin if supported (else argv with the SEC1 length cap),
   `-m <model>`, JSON output if the CLI offers it (else parse text + exit code). **Account isolation:** each connection gets its own home dir
   (`meta.home`, default `FARM_DATA_DIR/ai/<id>`); set the env var(s) the CLI uses for its config/credentials dir (verify from `gemini --help`/docs —
   e.g. `GEMINI_CLI_HOME`, else `HOME`/`USERPROFILE` like the agy driver does) through the SEC1 env allow-list. Session continuation if the CLI
   supports resume (verify); otherwise the conversation layer replays the last N turns as context and says so (`session: replayed`).
   Errors: map quota/rate (429, "quota", "RESOURCE_EXHAUSTED") → `limit` with `retry_at` (daily reset at midnight Pacific for free tier);
   auth problems → `auth` (status needs_login). `farm ai login <id>` runs `gemini` interactively with that home so the owner signs in once.
2. **Free-first:** connections carry `meta.plan: free|paid` (default paid). For `ai="any"` or a pool, ranking prefers free accounts with quota
   left, then paid; an explicit `account` always wins. Daily request counters per account (`meta.daily_requests`, e.g. 1000) feed the existing
   quota ledger so an exhausted free account cools down until its reset instead of failing calls.
3. **RAM-safe global cap:** at most `settings.ai_max_parallel_cli` (default 4) CLI worker processes run at once across all accounts; extra jobs queue
   (FIFO, visible in `ai_status` as `queued: global cap`). Per-account `max_parallel` still applies.
4. `list_ais` shows plan tier, daily requests used/left and next reset per account.

## Acceptance tests (fake CLIs only; no network, no real logins)
- Fake `gemini` binary: two accounts with separate homes receive the right env and never the Farm's secrets; prompt delivered; JSON/text parsed;
  429 output → `limit` + `retry_at`, the next job goes to the other account.
- Free-first: two free + one paid account → jobs land on free accounts until their daily counter is exhausted, then paid; explicit `account` honoured.
- Global cap 2 with 5 jobs → never more than 2 fake processes alive at once; all 5 finish; queued reason reported.
- Existing AI tests (M3e, AIP2, SEC1) still pass.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 12 lines: files, tests, the verified Gemini CLI flags/env for account
isolation and resume, deviations.
