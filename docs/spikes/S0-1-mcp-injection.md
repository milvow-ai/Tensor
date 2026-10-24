# S0-1 — MCP injection into one-shot CLIs

Run 2026-10-24 on this PC against the running Farm (`http://127.0.0.1:8787/mcp`). The token is only ever referenced as `${FARM_TOKEN}`.

## Result
| CLI | Per-run Farm MCP + tool limit | Verified live |
|---|---|---|
| Claude Code | **Yes**: `--mcp-config <file> --strict-mcp-config --allowedTools ...` | yes: (a) allowed call works, (b) other tool denied |
| agy (Antigravity) | **Partly**: no flag. Config is global; a per-run copy works by overriding the home folder | config + permission syntax verified; Farm tools not reached (see below) |
| Codex | **Yes (help only)**: `-c mcp_servers.<name>.*=` overrides + `CODEX_HOME` | config parse verified, no live call (logged out, as briefed) |
| Hermes | **Yes**: a temporary `HERMES_HOME` with its own `config.yaml` | MCP connect verified; live call blocked (see S0-3) |

## Claude Code (`CLAUDE_CONFIG_DIR=D:/farm-data/ai/claude-02`)
`mcp.json` (Claude Code expands the header value from the environment; nothing secret in the file):
```json
{"mcpServers":{"harness-farm":{"type":"http","url":"http://127.0.0.1:8787/mcp","headers":{"Authorization":"Bearer ${FARM_TOKEN}"}}}}
```
Command (stdin redirected from `/dev/null`):
```
claude -p "<prompt>" --model haiku --output-format json --strict-mcp-config --mcp-config mcp.json \
  --allowedTools "mcp__harness-farm__farm_guide,mcp__harness-farm__get_capacity"
```
- **(a) allowed:** "Call mcp__harness-farm__get_capacity once…" gave `is_error:false`, `num_turns:3`, and an answer summarising the real pool state ("Claude 3 of 4 accounts usable… Codex none"). `permission_denials: []`. `duration_ms` 12223 (`duration_api_ms` 7661), `total_cost_usd` 0.00394.
- **(b) not allowed:** asking for `mcp__harness-farm__ask_ai` was refused; the JSON carries `permission_denials:[{"tool_name":"mcp__harness-farm__ask_ai",...}]`, `num_turns:3`, cost 0.00400. The worker saw the tool (it is not hidden) but could not run it. `--allowedTools` is a client-side gate, so the Farm should also enforce the allow-list server-side with the task token.
- `--strict-mcp-config` makes the worker ignore every other configured MCP server.
- **Flake:** 1 of 3 runs died with a Bun `Segmentation fault at address 0xBBADBEEF` (exit 3, empty stdout). The identical retry worked. The executor must treat exit 3 with no JSON as retryable.
- Attaching the MCP tools put ~14k tokens into the first-turn cache creation.

## agy (Antigravity)
- `agy mcp add|remove|list|enable|disable` writes **global** config (`~/.gemini/config/mcp_config.json`). I did **not** run `add` (it would change the owner's config). `agy --help` has no `--mcp-config` flag. `ANTIGRAVITY_CONFIG_REL_DIR` and project folders `.agents/.gemini/.agy/.antigravity` were tried: `agy mcp list` ignored them.
- **What works:** overriding the home folder. `USERPROFILE=<tmp> HOME=<tmp> agy mcp list`, with `<tmp>/.gemini/config/mcp_config.json` present, listed `harness-farm  http  enabled  http://127.0.0.1:8787/mcp` while the real config stayed empty.
- Permissions live in `<home>/.gemini/antigravity-cli/settings.json`: `{"permissions":{"allow":["mcp(harness-farm/get_capacity)","mcp(harness-farm/farm_guide)"]}}`. Grammar `mcp(<server>/<tool>)` was found in the binary; `cli.log` confirmed the rule was parsed (`permissions=&{Allow:[mcp(...)…`). Headless mode auto-denies anything not allowed (`denied_actions` in the JSON).
- **Not reached:** with the temporary home, `cli.log` says `Not reloading plugin MCP servers: ... You are not logged into Antigravity`, and 4 live prompts (`gemini-3.8-flash-high`) never called `call_mcp_tool`; the model tried `run_command` (denied). Per-run MCP for agy is therefore **not proven**. Remaining routes: a logged-in isolated home, or `agy mcp add` on a dedicated account (a product decision, since agy is single-account/global per CONTEXT §7).
- agy exposes all MCP tools through one generic `call_mcp_tool`.

## Codex 0.160.0 (help only)
- `CODEX_HOME=<tmp> codex mcp list --json -c 'mcp_servers.harness-farm.url="http://127.0.0.1:8787/mcp"' -c 'mcp_servers.harness-farm.bearer_token_env_var="FARM_TOKEN"'` showed a `streamable_http` server, `bearer_token_env_var: FARM_TOKEN`, `auth_status: bearer_token`. The token is read from the environment, never written.
- Tool limiting via `-c 'mcp_servers.harness-farm.enabled_tools=[...]'` was accepted but not shown by `mcp list`: **not verified**. `codex exec --ignore-user-config` and `-p <profile>` exist as isolation options.

## Recommendation
Claude Code: ship now (`--mcp-config` + `--strict-mcp-config` + `--allowedTools`) plus a server-side tool allow-list on the task token. Codex: `-c` overrides + `bearer_token_env_var`, verify live once an account is logged in. agy: keep it tool-less one-shot until a logged-in isolated home is proven.
