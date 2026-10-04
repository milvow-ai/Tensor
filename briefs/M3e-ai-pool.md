# Brief M3e — AI pool: main Claude delegates to other AIs (Claude accounts, Codex, Antigravity, Hermes) through the Farm MCP

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`, never read or copy any
CLI credential/auth file (e.g. `.credentials.json`, `auth.json`, `oauth_creds.json`) — only point each CLI at its own config directory.
Live calls allowed only to: `agy` (logged in) and `hermes -p farm-agent` (Bifrost VK, ≤ $0.05 total in this brief). Claude/Codex accounts are not logged in yet — test them with fakes.

## Goal (the owner's words, 2026-10-04)
"Main Claude asks for work it thinks Sonnet (or GPT, or Gemini) can do better; through the Harness Farm it gets access to Claude #2 and #3, Codex,
Antigravity… Each of these AIs keeps its own storage and memory. If one account errors or hits its limit, the next is ready so the machine never stops."
So: AI providers are pools of accounts like any tool pool. The Farm runs each AI through **its own unmodified CLI** in one-shot mode with that
account's own config dir, returns the answer, and stores only the trajectory (account, model, tokens, status, session id) — never transcripts.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §4.3, §4.7, `docs/HERMES-INTEGRATION.md` §14–15 (safety settings), `farm/executors/base.py`, `farm/resources/router.py`,
`farm/durable/batch.py` (if present), `config/registry.yaml` (AI pools), CLI help: `claude --help`, `agy --help`, `hermes --help`, and Codex after install
(`npm install -g @openai/codex` with `npm_config_cache=D:\dev-cache\npm`; record the version).

## Owns
`farm/executors/cli_agent/__init__.py`, `farm/executors/cli_agent/base.py` (common subprocess runner: argv list (no shell), cwd, env overlay, timeout + kill tree, stdout/stderr caps, JSON parse, usage extraction),
`farm/executors/cli_agent/claude.py` (`CLAUDE_CONFIG_DIR=<meta.config_dir>`; `claude -p <task> --output-format json --model <m>`; `--resume <session_id>`; mode `answer` = no tools (`--allowedTools ""` or the CLI's equivalent read-only setting), mode `edit` = `--permission-mode acceptEdits` confined to the given `cwd`; parse `result`, `session_id`, `usage`, `total_cost_usd`, `is_error`; detect usage-limit / auth errors from the JSON/result text → LIMIT_REACHED with `reset_at` parsed when present, NEEDS_LOGIN for auth),
`farm/executors/cli_agent/codex.py` (`CODEX_HOME=<meta.config_dir>`; `codex exec` non-interactive JSON mode; resume support; same classification),
`farm/executors/cli_agent/agy.py` (`agy -p … --output-format json --model … [--conversation <id>]`; multiple Google accounts: discover from `agy --help`/docs whether a config-dir env or flag exists; if not, document that only one agy account is supported now),
`farm/executors/cli_agent/hermes.py` (`hermes -p <profile> -z … --usage-file …`, TERMINAL_CWD pinned, `--resume`),
`farm/capabilities/schemas.py` (extend: `AskAiIn(ai: Literal['claude','codex','gemini','hermes','any'], model: str | None, task: str, mode: Literal['answer','edit']='answer', cwd: str | None, session_id: str | None, timeout_s: int = 900, json_schema: dict | None)`, `AskAiOut(text, json, ai, model, connection_id, session_id, usage, cost_usd, duration_s)`),
router hooks needed for AI: **session stickiness** (if `session_id` given → `ai_sessions` lookup → pin that connection; unknown → error), model → connection filter (connection `meta.models` must include the requested model), writing `ai_sessions` on success,
gateway tools: `ask_ai`, `ask_ai_batch(tasks[])` (uses `route_batch` if available, else bounded `asyncio.gather` over the pool respecting gates), `list_ais()` (per AI: accounts, status, models, cooldown/reset times, today's calls),
CLI: `farm ai login <connection>` (prints and runs the exact interactive login for that account: e.g. sets `CLAUDE_CONFIG_DIR` and launches `claude` so the owner types `/login`; creates the config dir under `FARM_DATA_DIR/ai/<id>`), `farm ai test <connection>` (one tiny call), `farm ai list`.
Tests: `tests/test_cli_agent_*.py` (fake CLIs: small Python scripts placed on a temp PATH that emulate each CLI's JSON output, limit errors, auth errors, timeouts, resume), `tests/test_accept_m3_ai_pool.py`.

## Acceptance tests
1. `ask_ai(ai='claude', model='sonnet', task=…)` over the in-memory MCP client → answered by `claude-02` (fake CLI), envelope + `session_id`; trajectory has account + model; nothing of the answer text is stored in the DB except the returned envelope cache when cache_ttl > 0 (it is 0 for ask_ai).
2. Batch of 3 tasks spreads over ≥ 2 Claude accounts (concurrency 1 each).
3. Force `claude-02` to return a usage-limit error with a reset time → it goes `exhausted` with `next_reset_at`, the task is retried on `claude-03` and succeeds; nothing lost; `list_ais()` shows the reset time.
4. Follow-up with the returned `session_id` lands on the same account (`--resume` argv asserted); if that account is exhausted → clear error `session_account_unavailable` (no silent switch, because the other account has no such session).
5. Auth failure → `needs_login` + an alert row whose message contains the exact `farm ai login <id>` command.
6. Live smoke (marked `@pytest.mark.live`, excluded from check.ps1, run once and report): `farm ai test agy-01` and `farm ai test hermes-01` both return an answer; report cost.

## Done when
`scripts/check.ps1` → `RESULT: all passed`; the two live smokes pass; `uv run farm ai list` prints all AI accounts with their states.

## Reply (≤ 15 lines)
Files changed; test count; check tail; per-CLI facts discovered (flags, JSON fields, limit/auth error formats, multi-account mechanism) — these go into CONTEXT; live smoke cost; deviations.

## Phase A (run now, in parallel with M1) — executors only
Do ONLY: `farm/executors/cli_agent/*` (all five modules), the fake-CLI test harness, `tests/test_cli_agent_*.py`, the Codex install + per-CLI fact discovery, and the two live smokes driven directly through the executors (not via MCP).
If `farm/executors/base.py` does not exist in your worktree yet, create it **exactly** as CONTEXT §4 specifies (a parallel builder owns the real one; at merge the lead keeps theirs, so do not add anything beyond §4).
Do NOT touch capability schemas, router, gateway, CLI or registry in Phase A — they are Phase B (after M1c lands).
Done when: `uv run pytest tests/test_cli_agent_*.py -q` passes and ruff/mypy are clean on `farm/executors/cli_agent` (`uv run ruff check farm/executors/cli_agent tests` and `uv run mypy farm/executors/cli_agent`).
Reply additionally with: a "CLI facts" block (≤ 12 lines) for CONTEXT — exact argv, JSON fields, limit/auth error formats, multi-account mechanism per CLI.

## Phase B (run now — router and gateway exist)
Phase A executors are merged (`farm/executors/cli_agent/*`, thread-based runner). The router (`farm/resources/router.py`) is generic: it dispatches by provider `executor` kind and the gateway generates one MCP tool per entry in `farm/capabilities/schemas.py:CAPABILITY_MODELS` (today `ask_ai` is skipped with "no input/output models yet").
Do: add `AskAiIn`/`AskAiOut` to `CAPABILITY_MODELS` (so the `ask_ai` tool appears); register the `cli_agent` executor kind in the executor registry (`farm/context.py`) mapping `meta.cli` → driver; router hooks: session stickiness (`session_id` → `ai_sessions` → pin that connection; missing → `session_unknown`; account not eligible → `session_account_unavailable`, never a silent switch), model filter (`meta.models` must contain the requested model, else skip with reason `model_not_offered`), `ai` field → provider pool (`any` = route order), write/refresh `ai_sessions` on success; LIMIT_REACHED with `reset_at` → connection `exhausted` until reset (health already supports it — reuse); gateway tools `ask_ai_batch(tasks)` (bounded concurrency across the pool; per-task envelopes) and `list_ais()`; CLI `farm ai login|test|list`; tests `tests/test_accept_m3_ai_pool.py` with the six acceptance cases (fake CLIs on PATH via the Phase A harness) + the live smoke for agy-01 and hermes-01 (marked live; run once; report cost).
Windows: never `asyncio.create_subprocess_*`; the Farm runs on the selector loop.
Done when: `scripts/check.ps1` → RESULT: all passed; `uv run farm ai list --local` prints every AI account with state; live smokes reported.
