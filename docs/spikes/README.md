# S0 spikes — summary (2026-10-24)

| Spike | Verdict | One line |
|---|---|---|
| [S0-1 MCP injection](S0-1-mcp-injection.md) | Claude **works**; agy partly; Codex and Hermes: mechanism found | Claude `--mcp-config --strict-mcp-config --allowedTools` proven both ways: the allowed call ran, `ask_ai` was denied. |
| [S0-2 ACP](S0-2-acp.md) | Claude **works**; Gemini, Codex, Hermes blocked | Warm turn 1.4 s versus ~17 s one-shot; a permission request arrives per MCP tool. |
| [S0-3 Hermes API agent](S0-3-hermes-api-agent.md) | Partly | A temp `HERMES_HOME` injects the Farm MCP (connect proven); the live call got 401 because the provider key is only in the real `.env`. |
| [S0-4 Metering](S0-4-metering.md) | Table done for Claude CLI/ACP and agy; Hermes fields listed | Claude cost is notional list price; agy reports no cost; cached and reasoning tokens are separate except agy has no cache-write. |

**Recommendation for ADR 14:** build A1 on Claude Code with per-run `--mcp-config` (one-shot) and the Claude ACP adapter for warm project sessions, both behind a server-side tool allow-list on the task token. Use Hermes with a per-run temp `HERMES_HOME` as the API-agent runtime once the runner injects the provider key. Gemini and Codex ACP and agy MCP stay "not verified" until a key or login exists. Retry `claude -p` exit-3 segfaults.
