# S0-3 — API agent through Hermes

## Result: partly. Injection works without touching a profile; the live model call is blocked by the provider key, not by the mechanism.

- **Mechanism:** `HERMES_HOME=<temp dir>` makes Hermes read `<temp>/config.yaml` instead of the real profile. `${VAR}` placeholders are interpolated (`tools/mcp_tool_config.py` in the Hermes install), so the token stays in `FARM_TOKEN`. Temp config used (nothing secret):
```yaml
model: {default: zai.glm-5, provider: custom:bedrock-mantle}
providers:
  bedrock-mantle: {base_url: https://bedrock-mantle.us-east-1.api.aws/v1, key_env: AWS_BEARER_TOKEN_BEDROCK, name: Amazon Bedrock Mantle}
mcp_servers:
  harness-farm:
    url: http://127.0.0.1:8787/mcp
    headers: {Authorization: "Bearer ${FARM_TOKEN}"}
    enabled: true
    tools: {include: [farm_guide, get_capacity]}
```
- **Verified:** `HERMES_HOME=<temp> hermes mcp list` showed `harness-farm … 2 selected … enabled` (the include filter works). `hermes mcp test harness-farm` reported `Connected (10828ms)`, 21 tools discovered. No real profile was changed.
- **Live run:** `hermes -z "Call the farm_guide tool, then reply with one sentence." -m zai.glm-5 --provider custom:bedrock-mantle --usage-file usage.json` exited 2 after 68 s: `Custom endpoint rejected your API key … HTTP 401: Invalid bearer token`. The temp home has no `.env`; the provider key (`AWS_BEARER_TOKEN_BEDROCK`) lives in the real Hermes `.env`, which this spike must not read or copy, and it is not in the process environment. The `farm_guide` call and `zai.glm-5` behaviour are therefore **not verified**.
- Other facts: the Farm executor passes `--toolsets ""` today; `-t` selects toolsets; `--ignore-user-config` and `--safe-mode` (disables MCP) exist. The real profile also carries `figma-bridge` and `vercel` MCP servers, so a clean `HERMES_HOME` is also how to keep the worker's tools to the Farm only. `hermes acp` accepts MCP servers in `session/new` but stalled with the real profile (S0-2).

## To finish (needs the owner)
Export the provider key in the run's environment (later: a Bifrost virtual key) and re-run the command above with `HERMES_HOME` pointing at the temp folder; expect a `usage.json` with real tokens.

## Recommendation
The Farm runner generates a per-run temp `HERMES_HOME` (config + `FARM_TOKEN` in env + provider key injected as env by the runner, never in a file). This matches HERMES-INTEGRATION's one-HERMES_HOME-per-slot design.
