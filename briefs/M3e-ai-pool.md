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
