# Harness Farm Operations Guide

This guide covers running Harness Farm 24/7 as an agency service, account authentication, backups, and alert resolution.

---

## 1. 24/7 Operation & Service Installation

The Farm runs continuously in the background, supervised by an auto-recovering watchdog.

### Service Installation (Task Scheduler)
To install the per-user scheduled task that starts the watchdog on logon:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-farm-service.ps1
```

To test the installation configuration without writing changes:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-farm-service.ps1 -TestMode
```

To uninstall:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-farm-service.ps1 -Uninstall
```

### Watchdog Behavior
The watchdog (`scripts/farm-watchdog.ps1`):
* Polls `http://127.0.0.1:8787/health` every 15 seconds.
* If `/health` fails or hangs for >= 60 seconds, it restarts `farm run` and sends a critical alert (`farm_down`).
* Keeps the workstation awake via Windows `SetThreadExecutionState` so background AI jobs complete uninterrupted.

---

## 2. Account Logins and Credentials

The Farm never stores raw credentials in configuration files or code.

### Storing API Secrets
Use `farm set-secret` to write credentials to `.env` with a masked prompt:
```powershell
uv run farm set-secret FARM_MCP_NOTION_TOKEN
uv run farm set-secret CLAY_API_KEY
```

### Interactive AI CLI Logins
For AI worker accounts (Claude, Codex, Antigravity, Hermes):
```powershell
# Interactive login for Claude account 02
uv run farm ai login claude-02

# Interactive login for Codex account 01
uv run farm ai login codex-01

# Interactive login for Antigravity
uv run farm ai login agy-01
```

### OAuth MCP Server Logins
For third-party OAuth MCP integrations:
```powershell
uv run farm mcp login notion-01
```

---

## 3. Database Backups and Rotation

Harness Farm includes automated database backups to prevent data loss.

### Running a Backup
```powershell
uv run farm backup
```

Backups are saved to `<FARM_DATA_DIR>/backups/` as timestamped `.sql` / `.dump` files.
A 14-day rotation policy automatically prunes older archives.

### Verifying System Health
Run `farm doctor` at any time to verify system health across DB, migrations, accounts, circuits, disk space, and CLIs:
```powershell
uv run farm doctor
```

---

## 4. Alert Reference & Remediation

Alerts are stored in `public.alerts` and dispatched to Telegram when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are configured. All alerts deduplicate identical occurrences for 1 hour.

| Alert Kind | Severity | Meaning | Remediation |
|---|---|---|---|
| `farm_down` | Critical | Farm HTTP endpoint `/health` failed for >= 60s or crashed. | Check watchdog logs in `<FARM_DATA_DIR>/logs/watchdog.log`. Verify Postgres status with `farm db check`. |
| `needs_login` | Critical | An account was rejected with an auth error or session expiration. | Run interactive re-login: `farm ai login <conn>` or `farm mcp login <conn>`, then activate the connection in Console or CLI. |
| `account_exhausted` | Warn | Account limit reached. Connection paused until next reset time. | Check `farm status` for reset timestamp. Add credits or additional accounts to the provider pool if needed. |
| `circuit_open_long` | Warn | Provider circuit breaker has remained open for > 15 minutes due to consecutive errors (5xx / network). | Check provider status page. Test connection with `farm commands` or CLI `farm call`. |
| `budget_threshold` | Info / Warn / Critical | Monthly spend crossed 50% (info), 80% (warn), or 100% (critical) threshold. | Review spend in Console or `farm status`. Increase budget via `farm commands` or registry if appropriate. |

To acknowledge an alert:
```powershell
uv run farm commands # or ack via Console / database
```
