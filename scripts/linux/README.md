# Linux Operations Scripts

| Script | Replaces | How to run |
|--------|----------|------------|
| `check.sh` | `scripts/check.ps1` | `./scripts/linux/check.sh` — runs ruff, mypy, pytest, secret scan. Set `CHECK_ONLY_SECRETS=1` to skip ruff/mypy/pytest. |
| `farm-watchdog.sh` | `scripts/farm-watchdog.ps1` | `./scripts/linux/farm-watchdog.sh` — one check per timer run, persists state between runs |
| `start-bifrost.sh` | `scripts/start-bifrost.ps1` | `./scripts/linux/start-bifrost.sh` — starts Bifrost gateway on port 8080 |

## Service units (deploy/systemd/)

| Unit | Purpose |
|------|---------|
| `harness-farm.service` | Main Farm service (`farm run`) |
| `harness-farm-watchdog.service` | Watchdog service (oneshot, runs once per timer) |
| `harness-farm-watchdog.timer` | Runs watchdog every minute |

Install: `cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/ && systemctl daemon-reload`
