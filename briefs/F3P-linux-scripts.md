# F3P — Linux versions of the operations scripts (for the server deployment)

Rules for every step: create only the files under **Owns**. Never edit an existing file. Never run `git add / commit / push /
reset / checkout / stash`, deploy, install system packages, start or stop services, delete anything, or send data off this
machine. Never read or print any `.env` file. Run every command in the foreground and wait for it.

**Goal.** Harness Farm will run on Linux servers. Its operations scripts exist only for Windows PowerShell today. Write Linux
equivalents with the **same behaviour**, so the server deployment (milestones F3 and A4) starts from tested scripts.

**Read first, completely:** `scripts/check.ps1`, `scripts/farm-watchdog.ps1`, `scripts/install-farm-service.ps1`,
`scripts/start-bifrost.ps1`, and `docs/ARCHITECTURE.md` §10. Understand what each one does before writing anything.

**Create:**
1. `scripts/linux/check.sh` — same checks and the same final line as `check.ps1` (`RESULT: all passed` on success, and a non-zero
   exit code on any failure), using `uv run`.
2. `scripts/linux/farm-watchdog.sh` — same health logic as `farm-watchdog.ps1` (probe `http://127.0.0.1:8787/health`, the same
   thresholds and restart decision), but it restarts through `systemctl restart harness-farm` instead of killing processes.
3. `deploy/systemd/harness-farm.service` — runs `farm run` as a dedicated user `harness`, `WorkingDirectory=/opt/harness-farm`,
   `EnvironmentFile=/etc/harness-farm/farm.env`, `Restart=on-failure`, `RestartSec=5`, and hardening: `NoNewPrivileges=true`,
   `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `ReadWritePaths=/var/lib/harness-farm`.
4. `deploy/systemd/harness-farm-watchdog.service` + `deploy/systemd/harness-farm-watchdog.timer` — runs `farm-watchdog.sh` every
   minute.
5. `scripts/linux/start-bifrost.sh` — the same command as `start-bifrost.ps1`, with Linux paths.
6. `scripts/linux/README.md` — one table: script → what it replaces → how to run it.

**Bash rules:** `#!/usr/bin/env bash` and `set -euo pipefail` in every script; quote every variable; no hard-coded Windows paths;
data paths from environment variables with Linux defaults (`FARM_DATA_DIR` defaults to `/var/lib/harness-farm`); one comment
line at the top of each file saying which PowerShell script it mirrors.

**Checks you must run (inside WSL, from this worktree):**
- `wsl -d Ubuntu-24.04 -- bash -n scripts/linux/check.sh` (and the same for every `.sh` file) — no syntax errors.
- `wsl -d Ubuntu-24.04 -- systemd-analyze verify --man=no deploy/systemd/harness-farm.service` (and for the watchdog unit and
  timer) — report its output; a warning that the binary path or user does not exist on this machine is expected and fine.

**Owns (new files only):** `scripts/linux/check.sh`, `scripts/linux/farm-watchdog.sh`, `scripts/linux/start-bifrost.sh`,
`scripts/linux/README.md`, `deploy/systemd/harness-farm.service`, `deploy/systemd/harness-farm-watchdog.service`,
`deploy/systemd/harness-farm-watchdog.timer`.

**Must not touch:** every existing file.

**Done when:** all files exist, `bash -n` passes for every script, and `systemd-analyze verify` shows no errors other than the
expected missing-path or missing-user warnings.

**Reply (15 lines or fewer):** files created · the check outputs · anything in the PowerShell scripts you could not mirror and why.
