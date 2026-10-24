# Linux server kit

The Farm on a Linux server for the Milvow team: [docs/RUNBOOK-SERVER.md](../../docs/RUNBOOK-SERVER.md) is the
procedure. Scripts and units keep LF line endings and the executable bit in git (`.gitattributes`; checked by
`tests/test_linux_server.py`).

| Script | Does | Run |
|--------|------|-----|
| `install-server.sh` | packages, user `farm`, code + venv in `/opt/harness-farm`, `/etc/harness-farm/farm.env`, units, `/usr/local/bin/farm`, swap, Tailscale, firewall; starts nothing | root, once (safe to repeat) |
| `farm` | the Farm's CLI as user `farm` with the service's settings (installed as `/usr/local/bin/farm`) | any admin shell |
| `update.sh` | state backup → git fast-forward → `uv sync` → `farm db backup` → `farm db migrate` → restart → `/health`, rollback on failure | root |
| `backup-state.sh` | age-encrypted snapshot of `/var/lib/harness-farm` (logins, token stores, secrets, registry) with a checksum manifest, 14 days | `harness-farm-backup.timer` |
| `restore-state.sh` | `--drill` (verify the latest backup), `--to DIR`, `--into-live` | root, with the owner's age key |
| `farm-watchdog.sh` | mirrors `scripts/farm-watchdog.ps1`: one health check per run, restarts a hung Farm or Bifrost, alerts | `harness-farm-watchdog.timer` |
| `start-bifrost.sh` | mirrors `scripts/start-bifrost.ps1`: pinned Bifrost on 127.0.0.1:8080 | `harness-farm-bifrost.service` |
| `check.sh` | mirrors `scripts/check.ps1`: ruff, mypy, pytest (with `tests/quarantine.txt`), secret scan; `CHECK_ONLY_SECRETS=1` scans only | from the repo |

## Units (`deploy/systemd/`)

| Unit | Runs |
|------|------|
| `harness-farm.service` | `farm run` as user `farm`, `Restart=always`, hardened (`ProtectSystem=strict`, writes only `/var/lib/harness-farm`) |
| `harness-farm-bifrost.service` | Bifrost as user `farm` (enable only when Hermes is used) |
| `harness-farm-watchdog.timer` / `.service` | the watchdog every minute (root: it restarts units; the alert is sent as `farm`) |
| `harness-farm-backup.timer` / `.service` | the state backup every day at 03:30 |

`install-server.sh` installs and enables them; settings come from `/etc/harness-farm/farm.env`
(template: `deploy/farm.env.example`).
