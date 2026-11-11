# Harness Farm

Harness Farm is a private team's resource manager and MCP/AI-worker orchestrator. Connect an AI IDE or CLI once to reach configured MCP tools, account pools and workers. The operator keeps the reasoning; the Farm routes work, tracks usage and stores results.

Tensor and Fosa are consumers of the Farm. Neither defines its product scope.

## Current release target

One private VPS for three people, with private databases, CLI logins, project context and memory, plus explicitly shared team resources. See [the owner-approved release requirements](docs/PRIVATE-TEAM-RELEASE.md) for the chosen personal/shared instance architecture and evidence required before deployment. This repository is under active hardening; the presence of an installer is not a production readiness claim.

## Development

Backend: Python 3.12 with uv. Console: Node.js with pnpm. Dependencies are pinned in `uv.lock` and `console/pnpm-lock.yaml`.

```powershell
uv sync --frozen
uv run farm --help
```

Start with `.env.example` and `config/registry.example.yaml`; keep credentials out of source control. Use a disposable data directory and an empty environment file for tests so they cannot touch the running Farm's state.

- [IDE/MCP connection guide](docs/CONNECT-IDES.md)
- [Operations and diagnostics](docs/FARM-OPERATIONS.md)
- [Existing Linux server runbook](docs/RUNBOOK-SERVER.md) — the original single-instance procedure; private-team isolation requirements take precedence
- [Private team deployment](docs/PRIVATE-TEAM-DEPLOY.md) — isolated profiles, consoles, enrollment, reporting and recovery
- [Console setup and data flow](console/README.md)
- [Quality gates and defect register](docs/QUALITY.md)

`farm doctor` runs diagnostics. `/health` is process liveness. Authenticated `/ready` checks the database, schema and background task liveness; it does not prove task progress or external provider availability.

## Verification

The existing backend gates are `scripts/check.ps1` and `scripts/linux/check.sh`. The console has `pnpm test`, `pnpm check` and `pnpm test:e2e`. Some backend tests require local PostgreSQL and subprocess/socket permissions; a sandbox failure is not a passing check. `tests/quarantine.txt` lists nonblocking tests separately from the regular gate.

Do not run two Farm processes against the same database: account concurrency and maintenance currently assume one process. A VPS worker cannot access a PC's project folder by passing its local path; it needs a server checkout or an authenticated local execution path.
