# S0-2 — ACP sessions

Client: `spikes/acp_client.py` (JSON-RPC over stdio: `initialize`, optional `authenticate`, `session/new` with the Farm MCP server, then N × `session/prompt` in one session; answers `session/request_permission` by an allow-regex). Raw reports: `D:/dev-cache/spikes/s0/s2/*.json`.

| Agent | Entry point | Result |
|---|---|---|
| Claude Code | `npx -y @zed-industries/claude-agent-acp` (0.23.1, deprecated, renamed `@agentclientprotocol/claude-agent-acp`) | **Works** |
| Gemini CLI 0.63.0 | `gemini --acp` | **Blocked at login.** `initialize` OK (`mcpCapabilities`: http, sse). `session/new` without auth: `-32000 Gemini API key is missing or not configured.` `authenticate(oauth-personal)` got no answer in 180 s (it waits for an interactive browser sign-in). Other methods (`gemini-api-key`, `vertex-ai`, `gateway`) need keys not available here. Stopped. |
| Codex | `npx -y @zed-industries/codex-acp` (0.16.0, deprecated, renamed `@agentclientprotocol/codex-acp`) | **Blocked.** `initialize` OK; `session/new`: `-32000 Authentication required` (accounts logged out; methods `chatgpt`, `codex-api-key`, `openai-api-key`). |
| Hermes 0.21.3 | `hermes acp` (extra, not in the brief) | **Stalled.** `initialize` OK in 10 s, then `session/new` gave no answer in 180 s. Its log shows it loaded the real profile's MCP servers (`registered 279 tool(s) from 2 server(s)`); the owner's `vercel` OAuth server is the likely blocker. Not resolved. |

## Claude ACP in detail (live, 2 prompts, 1 session)
- `initialize` 26 s (includes `npx` start), `session/new` 13 s (starts the MCP connection): paid once per session.
- MCP hand-over works: the Farm server was passed in `session/new.mcpServers` (`type:http`, `Authorization` header) and the agent called `mcp__harness-farm__get_capacity`.
- **Permission flow:** one `session/request_permission` for that tool (kind `other`; options `allow_always`, `allow_once`, `reject_once`). The client allowed it by name and the agent proceeded. This is the hook for Farm policy checks and approvals. Modes offered: `default, acceptEdits, plan, dontAsk, bypassPermissions`; models: `default, opus, haiku`.
- Turn 1 (with tool) **7.2 s**; turn 2 (same session, no tool) **1.4 s**.
- Tokens (`result.usage`): turn 1 in 7 / out 230 / cached-read 18 731 / cached-write 12 338; turn 2 in 3 / out 16 / cached-read 12 338 / cached-write 72.

## One-shot vs warm session (same machine, same tool call)
| | `claude -p` (S0-1, haiku) | ACP warm session |
|---|---|---|
| Tool-call task | 12.2 s CLI time (17–21 s wall incl. start) | 7.2 s first turn |
| Follow-up | new process, ~17 s, re-reads ~14k cached tokens | **1.4 s**, 12k cache-read, 72 new cache-write |
| Cost | $0.0039 (haiku) | cumulative `usage_update.cost` $0.0554 then $0.0596, so **$0.0042 for turn 2** (adapter default model) |

Models differ (ACP used the adapter's `default`), so compare the latency shape, not the dollars.

## Limits found
- Each agent needs its own login handling; two of three cannot start headless here without a person or a key.
- Both Zed adapter packages are deprecated; pin the new `@agentclientprotocol/*` names in A1.
- The Claude adapter ran `ToolSearch` before the MCP tool (deferred tool loading): one extra tool call per session.

## Recommendation
ACP is viable for Claude Code now (headless, MCP hand-over, clean permission requests, large warm-turn win). Gemini and Codex are "not verified" until a key or login exists. Hermes ACP needs an isolated `HERMES_HOME` to avoid the owner's other MCP servers.
