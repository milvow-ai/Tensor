# Brief M5 — 24/7 operation and hardening: run as a service, watchdog, doctor, backups, operations guide

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`. Do not change Windows
system/power/security settings; installing a per-user scheduled task is allowed only via a script the owner runs (`scripts/install-farm-service.ps1`) — do not run it yourself.

## Goal
The owner wants a machine that never stops (HANDOFF §9 M5 + owner 2026-10-04): the Farm process (`farm run`: gateway over HTTP for local clients +
manager scheduler + command consumer + DBOS worker) and Bifrost start automatically at Windows logon, are restarted by a watchdog if they crash or hang,
keep the PC awake while running, and alert by Telegram when anything is down, exhausted or needs a login. `farm doctor` proves health in one command.

## Read first
`briefs/CONTEXT.md`, `HANDOFF.md` §9 M5, §13 (Windows risks), `scripts/start-bifrost.ps1`, `farm/control/cli.py`, `farm/manager/*`, `farm/durable/*`.

## Owns
`farm/control/doctor.py` + CLI `farm doctor` (checks with PASS/WARN/FAIL lines and exit code: DB reachable + migrations at head, Bifrost health,
each pool has ≥ 1 active connection, needs_login list, open circuits, exhausted accounts with reset times, free disk ≥ 2 GB on C: and D:, FARM_DATA_DIR
writable, evidence dir size, last backup age < 26 h, keep-awake active, command consumer heartbeat < 60 s, AI CLIs found on PATH with versions),
`farm/control/heartbeat.py` (process writes a heartbeat row/file every 15 s), `farm/control/keepawake.py` (Windows `SetThreadExecutionState` while
`farm run` lives; no settings changes), `farm/control/backup.py` + CLI `farm backup` (pg_dump of the Farm DB to `FARM_DATA_DIR/backups/` with 14-day
rotation; in local mode use pgserver's bundled pg_dump), `scripts/farm-watchdog.ps1` (loop: if `farm run` or Bifrost is not healthy for 60 s →
restart it, log, Telegram alert via `farm alert send`), `scripts/install-farm-service.ps1` (creates a per-user Task Scheduler task "HarnessFarm" at logon
that runs the watchdog hidden; prints what it did; `-Uninstall` removes it), `docs/FARM-OPERATIONS.md` (install, first run, logins per account
(`farm ai login`, `farm mcp login`, `farm set-secret`), daily ops, adding tools/accounts via Console or CLI, backups/restore, troubleshooting, what each
alert means), tests `tests/test_doctor.py`, `tests/test_backup.py`, `tests/test_accept_m5_watchdog.py` (spawns `farm run` in a temp data dir, kills it,
runs one watchdog iteration in test mode, asserts restart within 60 s and an alert recorded).

## Done when
`scripts/check.ps1` → `RESULT: all passed`; `uv run farm doctor` runs on this PC and every FAIL is explained in the reply.

## Reply (≤ 15 lines)
Files changed; test count; check tail; `farm doctor` output; deviations.
