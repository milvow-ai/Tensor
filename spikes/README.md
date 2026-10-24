# spikes/

Throwaway tools for the S0 runtime spikes (results in `docs/spikes/`).

- `acp_client.py` — headless ACP client (stdlib only). Example:
  `python -I spikes/acp_client.py --out r.json --allow-tool get_capacity --prompt "..." --prompt "..." -- npx -y @zed-industries/claude-agent-acp`
  It reads the Farm token from `FARM_TOKEN`, sends it only to the agent as the MCP `Authorization` header, and never prints or stores it. `--auth-method` calls `authenticate`; `--no-farm` skips the MCP hand-over.
