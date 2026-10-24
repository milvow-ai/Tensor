# F3P-R1 — Fix the Linux scripts (review findings)

Rules: edit only the files named below (they are your own new files from F3P). Never edit any other file. Never run
`git add / commit / push / reset / checkout / stash`, install packages, start or stop services, delete anything, or send data
off this machine. Never read or print any `.env` file. Run every command in the foreground.

Your F3P files work in parts, but the review found these defects. Fix every item exactly as written.

1. **Shebang first.** In every `.sh` file, line 1 must be `#!/usr/bin/env bash`. Move the `# Mirrors …` comment to line 2.
2. **`scripts/linux/check.sh` — the secret scan never runs.** The pattern uses `(?!…)`, which `grep -E` does not support; grep
   errors and `|| true` hides it, so the step always passes.
   - Use `grep -PIn` (Perl regex) instead of `grep -En`.
   - Do not hide grep errors: grep exit code 0 = match, 1 = no match, 2 = error. Exit code 2 must make the step FAIL with the
     error text.
   - Add a self-test before scanning: run the same pattern against the string `sk-ant-` followed by 24 letters `a`. If it does
     not match, the step FAILs with "secret pattern self-test failed".
   - Add an environment switch: when `CHECK_ONLY_SECRETS=1`, run only the secret-grep step (skip ruff, mypy, pytest).
3. **`scripts/linux/farm-watchdog.sh` — one check per run, no loop.** A systemd timer starts it every minute, so it must check
   once and exit.
   - Remove the `while true` loop and `sleep`.
   - Keep the unhealthy counters between runs in a state file `${FARM_DATA_DIR}/watchdog/state` (two lines:
     `farm_unhealthy=<seconds>` and `bifrost_unhealthy=<seconds>`); create the folder if missing.
   - Each failed check adds `CHECK_INTERVAL_SECONDS` (default **60**); a healthy check resets that counter to 0.
   - Restart when a counter reaches `UNHEALTHY_THRESHOLD_SECONDS` (default **180**), then reset it to 0.
   - Exit code 0 always (a failed check is logged, not an error of the watchdog itself).
4. **Alerts:** call the Farm binary by path, not `uv`: `"${FARM_BIN:-/opt/harness-farm/.venv/bin/farm}" alert send …`.
5. **`deploy/systemd/harness-farm-watchdog.service`:** keep `Type=oneshot`; **remove `User=` and `Group=`** (it must run as root
   to restart a system service); keep `NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, `ProtectHome=true`,
   `ReadWritePaths=/var/lib/harness-farm`; `After=network-online.target harness-farm.service`.
6. **`deploy/systemd/harness-farm-watchdog.timer`:** replace `OnCalendar=*:00:00` and `Persistent=true` with exactly:
   `OnBootSec=1min`, `OnUnitActiveSec=1min`, `AccuracySec=5s`, `Unit=harness-farm-watchdog.service`.
7. **`deploy/systemd/harness-farm.service`:** `After=network-online.target` and `Wants=network-online.target`;
   `ExecStart=/opt/harness-farm/.venv/bin/farm run`; add `Environment=FARM_DATA_DIR=/var/lib/harness-farm`,
   `StateDirectory=harness-farm`, `LimitNOFILE=65536`; keep the hardening lines.
8. **README:** update the table for the changed behaviour (watchdog = one check per timer run; check.sh `CHECK_ONLY_SECRETS`).

**Checks you must run and report:**
- `wsl -d Ubuntu-24.04 -- bash -n <file>` for every `.sh` file.
- `wsl -d Ubuntu-24.04 -- systemd-analyze verify --man=no <unit>` for the three units (missing user / path warnings are fine).
- From this worktree in WSL: `wsl -d Ubuntu-24.04 -- bash -c "cd '/mnt/d/Harness Farm/wt-f3p' && CHECK_ONLY_SECRETS=1 bash scripts/linux/check.sh"`
  — must print `PASS  secret-grep` and `RESULT: all passed`.

**Owns:** `scripts/linux/check.sh`, `scripts/linux/farm-watchdog.sh`, `scripts/linux/start-bifrost.sh` (shebang only),
`scripts/linux/README.md`, `deploy/systemd/harness-farm.service`, `deploy/systemd/harness-farm-watchdog.service`,
`deploy/systemd/harness-farm-watchdog.timer`.

**Reply (15 lines or fewer):** each item 1–8 done or not · the three check outputs.

## Resume note
A previous run stopped early. The files listed by `git status` are its partial work: review them, keep what is right, fix what is not, finish the brief.

