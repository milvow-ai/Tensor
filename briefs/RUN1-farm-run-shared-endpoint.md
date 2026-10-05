# Brief RUN1 — `farm run`: one always-on Farm that every IDE/agent shares over MCP

Rules: follow `briefs/CONTEXT.md` §6 (incl. "Reuse first"). Touch only the files under "Owns". Never git add/commit/push. Never read `.env`. Telegram and
all providers are mocked in tests. Run commands in the foreground and report once.

## Goal (owner, 2026-10-05)
Harness Farm is the agency's central tool: **any IDE or agent** (Claude Code windows, Codex, Antigravity/Gemini CLI, Cursor, …) attaches over MCP and
instantly gets every connected MCP server, tool pool and AI worker. Several sessions must use **one** running Farm **at the same time** (e.g. one Claude
session runs research on Claude account 2 through the Farm while another session uses account 3). That needs one long-running process with a shared,
authenticated HTTP MCP endpoint, which starts with Windows, restarts on crash and alerts when something needs the owner.

## Read first
`briefs/M3b-account-manager.md` (budgets/hard stop, `farm run`, `set-secret`), `briefs/M5-247-hardening.md` (service, watchdog, alerts, ops guide),
`farm/gateway/server.py` + `middleware.py` (bearer auth already exists for HTTP), `farm/control/{cli,doctor,heartbeat,keepawake}.py`, `scripts/farm-watchdog.ps1`,
`scripts/install-farm-service.ps1`, the AIP2 job runner (`farm/ai/`) and OPEN1 sync (`farm/mcp/`) once merged, FastMCP HTTP docs in `library/fastmcp/docs/deployment/http.mdx`.

## Owns
`farm/control/run.py` (new: process composition), `farm/control/cli.py` (`farm run`, `farm connect`, `farm token`, `farm set-secret` if missing, `farm alert send`),
`farm/control/alerts.py` (Telegram sender + alert dedupe), `farm/resources/budget.py` (hard stop) + the minimal router hook call, `farm/settings.py` (run/http settings),
`scripts/farm-watchdog.ps1`, `scripts/install-farm-service.ps1`, `docs/FARM-OPERATIONS.md`, `docs/CONNECT-IDES.md`, tests `tests/test_run.py`,
`tests/test_connect.py`, `tests/test_budget_hard_stop.py`, `tests/test_alerts.py`, `tests/test_accept_run1.py`.

## Build
1. **`farm run`**: one process (SelectorEventLoop; threads for subprocesses per FIX1) that serves the gateway over **FastMCP streamable HTTP** on
   `settings.http_host` (default `127.0.0.1`) : `settings.http_port` (default 8787) path `/mcp`, and also runs: farm_commands consumer, AI job runner, MCP
   tool sync, heartbeat (doctor-visible), manager scheduler hooks that exist. Graceful shutdown on Ctrl+C/SIGTERM: stop accepting, let running calls finish
   up to 30 s, mark AI jobs per AIP2 rules. `farm serve` (stdio) keeps working for single-IDE use.
2. **Tokens per client**: `farm token create <client-name>` prints a new random token once and stores only its hash (+ name, created_at, last_used) in the
   DB; `farm token list|revoke`. The bearer middleware maps token → client name, and every run row records the client (so the Console shows which IDE/session
   called). No token in logs. Constant-time compare (keep existing).
3. **`farm connect <claude-code|codex|cursor|gemini|antigravity|generic>`**: prints the exact steps/snippet to attach that IDE to the running Farm, using an
   **env-var reference for the token** wherever the IDE supports it (never paste the token into a config file the tool writes). Verify each IDE's documented
   MCP config format from docs/READMEs available locally (e.g. `library/`), or mark that IDE's snippet "unverified" in the output. `--write` only for
   formats whose file location and env-var support are verified, and only after a y/N prompt.
4. **Budget hard stop**: before reserving a paid call, the router asks `budget.check()`; over a hard-stop budget → refused with a clear reason
   (`budget_exhausted`, which budget, current spend); soft budgets only alert at 50/80/100 % (one alert each per period). Free calls are never blocked.
5. **Alerts**: `farm alert send` + internal API; Telegram via bot token/chat id from env refs; dedupe identical alerts for 1 h; alert kinds: farm down,
   account needs login, account exhausted, circuit open > 15 min, budget thresholds.
6. **Service**: finish the watchdog + Task Scheduler installer for `farm run` (and Bifrost): restart within 60 s when `/health` fails, alert on restart.
7. **Docs**: `docs/CONNECT-IDES.md` (owner-facing: start the Farm, create a token per IDE, attach Claude Code / Codex / Cursor / Gemini, verify with one
   call) and `docs/FARM-OPERATIONS.md` (install service, logins per account, backups, what each alert means).

## Acceptance tests (no network beyond 127.0.0.1)
- Start `farm run` on a free port in a temp data dir; **three concurrent MCP HTTP clients** (different tokens) list tools and call tools at the same time;
  every result correct; run rows carry the right client names; a request without/with a wrong token → 401.
- Kill the process; one watchdog iteration in test mode restarts it within 60 s and records an alert.
- Hard-stop budget blocks a paid call with the reason and allows a free one; soft thresholds alert once each.
- `farm connect claude-code` output contains the URL and an env-var token reference, never a literal token.
- Graceful shutdown lets an in-flight call finish.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 15 lines: files, tests, gate tail, the verified/unverified status of each IDE snippet, deviations.
