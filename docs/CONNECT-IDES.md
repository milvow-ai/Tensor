# Connecting IDEs and Agents to Harness Farm

Harness Farm acts as the central agency tool hub: any IDE or agent (Claude Code, Codex, Cursor, Gemini CLI, Antigravity) connects over MCP to share all provider pools, accounts, and AI workers.

Multiple client sessions can connect to **one running Farm process** concurrently over Streamable HTTP.

---

## 1. Start the Farm

Start the always-on shared endpoint:

```powershell
uv run farm run
```

By default, this binds FastMCP Streamable HTTP to `http://127.0.0.1:8787/mcp`.
The server:
* Protects MCP endpoints with Bearer token authentication
* Exposes a `/health` endpoint for monitoring and watchdogs
* Runs background AI job workers, command consumers, and maintenance schedulers
* Keeps Windows awake while active

For headless local single-IDE use via stdio, `uv run farm serve` remains available.

---

## 2. Create a Token per Client

Each IDE, window, or agent session receives its own authenticated token. The Farm maps each token to the client's name so that Console and trajectories record exactly which session initiated each call.

Create a token for each client:

```powershell
uv run farm token create claude-window-1
uv run farm token create codex-research
uv run farm token create cursor-dev
```

The CLI prints the raw token once. Store it in your environment (never commit it to git):

```powershell
# Windows PowerShell
$env:FARM_TOKEN = "your-generated-token"

# Bash / Zsh
export FARM_TOKEN="your-generated-token"
```

To view or revoke tokens:

```powershell
uv run farm token list
uv run farm token revoke claude-window-1
```

---

## 3. Attach Claude Code (Verified)

To view the connection snippet:

```powershell
uv run farm connect claude-code
```

### Option A: Via Claude Code CLI
```bash
claude mcp add --transport http harness-farm http://127.0.0.1:8787/mcp --header "Authorization: Bearer ${FARM_TOKEN}"
```

### Option B: Automatic Configuration Write
```powershell
uv run farm connect claude-code --write
```
(Prompts for confirmation before updating `~/.claude.json` with an environment variable reference—never pasting the literal token).

### Option C: Manual Config (`~/.claude.json`)
```json
{
  "mcpServers": {
    "harness-farm": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8787/mcp",
      "headers": {
        "Authorization": "Bearer ${FARM_TOKEN}"
      }
    }
  }
}
```

---

## 4. Attach OpenAI Codex (Verified)

To view the connection snippet:

```powershell
uv run farm connect codex
```

### Automatic Configuration Write
```powershell
uv run farm connect codex --write
```

### Manual Config (`~/.codex/config.toml` or `$CODEX_HOME/config.toml`)
```toml
[mcp_servers.harness-farm]
url = "http://127.0.0.1:8787/mcp"
bearer_token_env_var = "FARM_TOKEN"
```

Codex natively resolves `bearer_token_env_var` from your process environment.

---

## 5. Attach Cursor (Unverified)

To view the connection snippet:

```powershell
uv run farm connect cursor
```

In Cursor, open **Settings > Features > MCP**, or edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "harness-farm": {
      "url": "http://127.0.0.1:8787/mcp",
      "headers": {
        "Authorization": "Bearer ${FARM_TOKEN}"
      }
    }
  }
}
```

*Note: Cursor's environment variable expansion inside headers is marked unverified. If Cursor does not substitute `${FARM_TOKEN}`, supply the token via a local wrapper or process environment.*

---

## 6. Attach Gemini CLI / Antigravity (Unverified)

To view the connection snippet:

```powershell
uv run farm connect antigravity
```

Add to `~/.gemini/config/mcp_config.json`:

```json
{
  "mcpServers": {
    "harness-farm": {
      "url": "http://127.0.0.1:8787/mcp",
      "headers": {
        "Authorization": "Bearer ${FARM_TOKEN}"
      }
    }
  }
}
```

---

## 7. Verify the Connection

Once attached in your IDE or agent, test the connection by invoking an infra tool:

```
Ask the agent: "Call the get_capacity tool on harness-farm"
```

Verify in the Farm database that the call was recorded under your client name:

```powershell
uv run farm status
```

The trajectory and `runs` row will display `caller: <client-name>` corresponding to the token used.
