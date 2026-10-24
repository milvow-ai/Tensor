# S0 — Runtime spikes: how any AI becomes an agent with the Farm's tools

Rules for every step: create only the files under **Owns**. Never edit existing product code. Never run `git add / commit / push
/ reset / checkout / stash`, deploy, or send data off this machine except the AI calls the steps require. Never read, print or
copy any `.env` file, token or API key: when a CLI needs the Farm token, reference the `FARM_TOKEN` environment variable that
already exists (e.g. `${FARM_TOKEN}` or `env:FARM_TOKEN` in a config) and never write its value anywhere. Do not change any
existing Hermes profile, Claude/Codex/Gemini login or global config: work in temporary folders under
`D:/dev-cache/spikes/s0/`. Keep every live AI prompt tiny (one sentence) — at most 20 live calls in total. Run every command in the
foreground.

**Goal.** `docs/AGENTS.md` §4 and §12 plan three agent runtimes. Before A1 builds on them, prove what actually works on this
machine, with evidence. Each spike ends with a one-page result: what works, the exact commands / configs, measured latency and
token use, limits found, and a recommendation.

**Read first:** `docs/AGENTS.md` (§0.2, §4, §8, §12) · `docs/ARCHITECTURE.md` ADR 14, 15 · `briefs/CONTEXT.md` §7 (CLI facts) ·
`farm/executors/cli_agent/{claude,codex,agy,hermes}.py` (how the Farm runs CLIs today) · `tests/fixtures/cli/README.md`.

**Spikes:**
1. **MCP injection into one-shot CLIs** (most important for A1). For Claude Code (`CLAUDE_CONFIG_DIR=D:/farm-data/ai/claude-02`):
   start `claude -p` with a temporary `--mcp-config` file that points at the running Farm (`http://127.0.0.1:8787/mcp`, header
   `Authorization: Bearer ${FARM_TOKEN}`) and `--allowedTools` limited to two Farm tools (`mcp__harness-farm__farm_guide`,
   `mcp__harness-farm__get_capacity`). Prove: (a) the worker can call an allowed Farm tool; (b) it cannot call a non-allowed one
   (e.g. `mcp__harness-farm__ask_ai`). Find the equivalent for agy (Antigravity) and Codex (Codex accounts are logged out — document
   the config mechanism from `codex --help` / its docs only; no Codex live calls) and Hermes.
2. **ACP sessions.** Check which ACP entry points exist here: `gemini --acp` (Gemini CLI is installed), the Zed adapters
   `@zed-industries/claude-agent-acp` and `@zed-industries/codex-acp` (run them with `npx -y` from the temp folder; do not install
   anything globally). With a tiny Python ACP client (JSON-RPC over stdio, in `spikes/acp_client.py`): initialize, open a session,
   pass the Farm MCP endpoint at session start, send one prompt, observe a permission request if any, then send a second prompt in the
   same session and measure the latency and token difference against a one-shot CLI run. Whatever fails (login, headless refusal),
   document the exact error and stop that path.
3. **API agent through Hermes.** Determine how Hermes can be given an MCP server for a single run **without changing an existing
   profile** (flag, environment variable, or a temporary profile copied into the temp folder via `HERMES_HOME` or similar). If it is
   possible, run one tiny prompt with model `zai.glm-5` (default profile, `--provider custom:bedrock-mantle`) that calls
   `mcp__harness-farm__farm_guide`. If it requires changing a real profile, stop and document the steps instead.
4. **Metering fields.** For every runtime that ran live in 1–3, record the exact usage / cost fields it reports (names, units,
   whether cached input tokens and reasoning tokens are separate, whether cost is real or notional for subscriptions). One table.

**Owns (new files only):** `docs/spikes/S0-1-mcp-injection.md`, `docs/spikes/S0-2-acp.md`, `docs/spikes/S0-3-hermes-api-agent.md`,
`docs/spikes/S0-4-metering.md`, `docs/spikes/README.md` (one-line summary + recommendation per spike), `spikes/acp_client.py`,
`spikes/README.md`. Temporary files only under `D:/dev-cache/spikes/s0/`.

**Must not touch:** every existing file; any global config, profile or login.

**Done when:** the five docs exist, each claim in them is backed by a command you ran (or marked "not verified" with the reason),
and no secret value appears in any file (run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1` — its
secret-grep step must pass; other steps may be skipped if they exceed your time limit).

**Reply (15 lines or fewer):** per spike: works / partly / no, with the one-line reason · the recommendation for ADR 14 · live calls
used.
