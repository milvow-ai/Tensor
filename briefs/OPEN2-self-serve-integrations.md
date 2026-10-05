# Brief OPEN2 — self-serve integrations: start blank, add any MCP server / AI account from the Console or CLI

Rules: follow `briefs/CONTEXT.md` §6 (incl. "Reuse first"). Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.
Run every command in the foreground and wait for it; never start one in the background and poll it. Report once.

## Goal (owner, 2026-10-05: "I don't want demo data — I want a blank dashboard and to add MCPs and connect AIs myself")
Today the owner's Farm would start with the sample providers in `config/registry.yaml` (Reoon, Apollo, Clay ×7, …) and the Console can only add an
*account* to a provider that already exists (`add_connection`). The owner must be able to: start from an **empty** Farm, then add **any new MCP
server** (stdio command or HTTP/SSE URL, env-var/header names, OAuth), **any AI CLI account** (Claude ×N on different emails, Codex, Gemini/agy,
Hermes) and an **OpenAPI provider** — from the Console *or* the CLI — and see it work end to end. Secrets never go through the Console.

## Read first
`briefs/CONTEXT.md`, `farm/control/commands.py` + the command consumer (INT1 contract; C3 added kinds), `farm/registry/models.py`
(`McpProviderSpec`, provider/connection specs), `farm/registry/` sync/export code, `farm/mcp/` (OPEN1 import + sync; reuse it), `farm/control/cli.py`
(`farm mcp import|sync|login`, `farm ai login`), Console `console/src/app/(main)/integrations/**`, `console/src/lib/farm/{fixtures,commands*,integrations-data}.ts`.

## Owns
`farm/control/commands.py`, the command consumer module, `farm/registry/*` (a writer for the owner's registry file), `farm/control/cli.py`
(new: `farm mcp add`, `farm ai add`, `farm provider remove`), `config/registry.yaml` (becomes the owner's file, starts blank),
`config/registry.example.yaml` (new: today's sample content moves here), tests that read the sample registry (point them at the example file or a
fixture), new tests `tests/test_self_serve.py`, `tests/test_accept_open2.py`; Console: `console/src/app/(main)/integrations/**`, empty states on every
screen, `console/src/lib/farm/*` (contract regen, fixtures incl. an empty world), `console/e2e/open2-self-serve.spec.ts`.

## Build
1. **Blank start.** Move the sample providers/capabilities to `config/registry.example.yaml`. `config/registry.yaml` keeps only `settings` and the
   product-level capabilities every Farm needs (`ask_ai`, `agent_task`; MCP pass-through capabilities are created per MCP provider) — **no providers**.
   A migrated + synced database therefore has zero providers/connections. Migrations must not seed providers (check). The database is the source of
   truth for owner-added integrations; the registry file is kept in step by the writer (item 2) so `farm registry export`/`sync` round-trip.
2. **New command kinds** `add_provider`, `update_provider`, `remove_provider` (Pydantic contract in `commands.py`, regenerate the Console contract) handled
   by the consumer with ONE shared function also used by the CLI: validate with the registry models (reject literal secret values — names/refs only),
   create provider + first connection (+ the `mcp:<provider>` capability for MCP), write the owner's registry file, audit event, and for MCP run the
   OPEN1 tool sync and return the tool count in the command result. New tools must reach connected IDE sessions without a Farm restart (use OPEN1's
   refresh path; if a restart is unavoidable, say so in the result and the UI). `remove_provider` refuses while jobs/reservations are open unless `force`.
   Kinds: **MCP server** (stdio: command, args, env var NAMES, cwd; http/sse: url, header NAME→env ref; auth none|env|oauth; namespace; exposure),
   **AI CLI** (claude | codex | gemini (agy) | hermes: account id, label, allowed models, max_parallel; each account its own config dir/profile),
   **OpenAPI** (spec URL/path, auth env-var name). New accounts start `needs_login` (oauth/CLI) or `active` (env ref present) — never a fake "active".
3. **CLI parity:** `farm mcp add <name> (--command CMD [--arg A ...] | --url URL) [--env NAME ...] [--header NAME=ENV] [--auth none|env|oauth]`,
   `farm ai add <claude|codex|gemini|hermes> <account-id> [--label L] [--model M ...]`, `farm provider remove <id> [--force]` — all through item 2's
   function. After each add, print the next step (`farm set-secret NAME`, `farm mcp login <id>`, `farm ai login <id>`).
4. **Console.** "Add Integration" first asks *what*: New MCP server · New AI account · Account for an existing integration · OpenAPI provider; forms
   come from the registry JSON Schema (RJSF, already used) per kind; after save show status + the exact copy-paste next step; the new integration
   appears with its live state (needs_login → active after `test_connection`). **Every screen has a real empty state for a blank Farm** (Overview,
   Integrations, AI Pools, Runs, Billing, Routing, Memory): short guidance + the primary action ("Add your first MCP server"). Demo data appears only
   with `FARM_DATA_SOURCE=fixtures`; add an empty fixtures world (`FARM_FIXTURES=empty`) for e2e of the blank flow; fixtures must handle `add_provider`.

## Acceptance tests (no network; fake MCP server + fake CLIs)
- Blank registry → migrate + sync → 0 providers; gateway lists infra + AI job tools only; `ask_ai` capability exists.
- `add_provider` (stdio fake MCP) via the command queue → provider, connection, capability rows; tools synced; a connected MCP client sees
  `<ns>__echo` (without restart, or the documented reload); registry file updated; audit row; literal secret rejected; `remove_provider` cleans up.
- `farm mcp add` and `farm ai add claude claude-02` produce the same rows as the commands; AI account is `needs_login` until `test_connection` passes.
- e2e (empty world): every screen shows its empty state; Add MCP server enqueues `add_provider` and the integration appears; Add AI account shows the
  `farm ai login` command; existing e2e specs keep passing on the normal fixtures world.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed` and `pnpm check` + `pnpm test:e2e` green in `console/`. Reply ≤ 15 lines: files, tests,
gate tails, whether new MCP tools appear without restart, deviations.

## Resume note (lead, 2026-10-05 ~17:30)
The first run (Gemini) stopped at the 60-minute limit; its work (31 files) is in `git status` — keep it and finish. Before anything else run
`git merge claude/awesome-archimedes-vb1t7y` (main has AIP2b's migration 0011 and a fixed watchdog test); give your migration the next free number.
Gate failures to fix:
- ruff E501: `farm/control/cli.py:798, 963`; `farm/control/commands.py:112, 505, 544`.
- mypy: `commands.py:547, 914, 922` (indexing a possibly-None row: check for None first); `commands.py:1575/1577` (variable typed `CliAgentExecutor`
  receives `McpExecutor` / passed to `sync_all`: look the MCP executor up with the right type); `commands.py:1598` (payload typed `BaseModel`
  passed where the add/update/remove payload union is expected: narrow it).
- pytest: `tests/test_accept_open2.py::test_accept_open2_add_provider_stdio_fake_mcp_dynamic_echo`,
  `::test_accept_open2_cli_parity_and_test_connection`, `tests/test_self_serve.py::test_add_provider_rejects_raw_secrets`,
  `::test_cli_mcp_and_ai_add_parity_and_next_steps`, `::test_remove_provider_guards` (psycopg errors), plus the rest of the 8 the gate lists —
  run `uv run pytest tests/test_self_serve.py tests/test_accept_open2.py -x -q` to see them. Fix code, not tests, unless a test contradicts the brief.
- secret-grep: `tests/test_accept_open2.py:95` has a literal key-shaped string (`sk-ant-…`). Build fake keys at runtime (e.g. `"sk-" + "ant-" + "x" * 24`)
  so the scanner never sees one.
- Console e2e failed — run `pnpm --prefix console test:e2e` and fix (any spec that changes shared fixture state must restore it).
A real `farm run` is serving on 127.0.0.1:8787 on this PC: tests must never use that port or the shared `D:/farm-data` heartbeat/logs (use temp
dirs and free ports). Run every command in the foreground and wait for it; never start one in the background and poll it.

## Resume note 2 (lead, 2026-10-05 ~18:05) — what is left
Fixed already: mypy, secret-grep, tests/test_self_serve.py. Left:
- ruff E501 `farm/registry/sync.py:452`.
- `tests/test_registry.py::test_missing_required_sections_are_named` and
  `tests/test_registry_sync.py::test_export_of_an_unsynced_database_is_a_clear_error` encode the OLD rule (providers required / empty DB invalid).
  The blank start makes `providers` optional and an empty Farm valid — update both tests to the new rule (settings still required; exporting a
  database that was never migrated/seeded stays a clear error) and say so in the reply.
- `tests/test_registry_sync.py::test_sync_then_export_round_trips[example|fixture]`: export after sync no longer equals the input — the export's
  `capabilities` differ (it has the auto-ensured `agent_task`, and the input's own capabilities such as `verify_email` do not match). Round-trip must
  stay exact: don't inject product-level capabilities into a registry file that didn't declare them (ensure them in the DB at sync time, but export only
  what the file owns, or mark them `builtin` and skip them on export), and find why the declared capabilities differ.
- Console e2e (`pnpm --prefix console test:e2e`) fails — run it, fix, keep shared fixture state restored.
