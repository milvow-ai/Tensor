# Brief GUIDE1b — every session knows the Farm's capabilities and can request a missing integration

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.
Run every command in the foreground and wait for it; never start one in the background and poll it. Report once.
A real `farm run` serves on 127.0.0.1:8787 (data in D:/farm-data) and the live Console holds port 3100: tests never use them (`FARM_E2E_PORT`).

## Goal (owner, 2026-10-07)
Any AI session in any project must (1) know exactly what Harness Farm offers right now and what kind of thing each capability is, and (2) when its
task needs something the Farm does not have (an MCP server, a CLI, an API, an account), **file an integration request** that the owner sees,
approves/supplies (credentials, account), the lead integrates, and the session then uses — without the owner pasting context between sessions.

## Owns
`farm/gateway/guide.py`, `farm/gateway/server.py` (register tools), `farm/control/integration_requests.py` (new), one migration (next free number),
`farm/control/cli.py` (`farm guide`, `farm requests list|show|resolve`, `farm connect <ide> --skill`), `docs/skills/harness-farm/SKILL.md`,
Console: `console/src/app/(main)/integrations/**` (a "Requests" tab), `console/src/lib/farm/*` (data + fixtures), `console/e2e/guide1b.spec.ts`,
tests `tests/test_guide.py`, `tests/test_integration_requests.py`.

## Build
1. **farm_guide polish**: label tools "read-only" / "changes data" (from annotations) and add "may spend credits" only when the provider marks it
   (Clay enrichment tools: `add-*-data-points`, `run_subroutine*`); list each server's key tools read-only/most useful first (get/list/search/query
   before create/update/delete); add a **"What Harness Farm is"** header: one MCP server (connector) your IDE attaches to; behind it: pass-through MCP
   servers (remote HTTP and local stdio), AI CLI workers (Claude Code, Codex, Gemini/Antigravity, Hermes), built-in Farm tools; plus the dashboard
   (web app) and the `farm` CLI (owner admin). Not a plugin; the skill file teaches sessions to use it.
2. **`request_integration(name, kind: "mcp"|"cli"|"api"|"account"|"other", purpose, task_context?, urgency?: "now"|"soon"|"later", links?)`** tool:
   stores a row in `integration_requests` (id, created_at, requested_by = client/caller, name, kind, purpose, context, urgency, links, status
   `open|in_progress|done|declined`, owner_note, resolved_at); returns the request id and "the owner will be notified; continue with what is
   available". Dedupe identical open requests (same name+kind) by adding the new purpose to the existing row. Alert the owner (existing alerts, kind
   `integration_request`). Never accept secrets in a request (reject key-shaped strings like the registry does).
3. **`list_integration_requests(status?)`** tool (read-only) so a session can see whether its request was resolved; `farm requests list|show|resolve`
   CLI; Console Integrations → **Requests** tab (table: name, kind, purpose, requested by, urgency, status; actions: mark in progress / done / declined
   with a note → via a new command kind `resolve_integration_request`).
4. **farm_guide** gets a final section **"Need something else?"**: call `request_integration(...)` with what you need and why; examples (an MCP server
   by name, a CLI, an API, another account for a pool).
5. **Skill** `docs/skills/harness-farm/SKILL.md` (frontmatter `name: harness-farm`, `description` saying when to use: delegate research/bulk/second
   opinions to AI workers; use connected MCP servers; request missing integrations): body = connect check, call `farm_guide` first, recipes, credit
   etiquette (read-only first, ask before credit-spending calls), the request flow. `farm connect <ide> --skill` prints the copy path (Claude Code
   `~/.claude/skills/harness-farm/SKILL.md`; Codex `AGENTS.md` snippet); `--write` only after y/N.
6. `farm guide` CLI prints the guide for humans.

## Acceptance tests (no network)
- farm_guide: labels correct (read-only / changes data / may spend credits), ordering read-only first, header + "Need something else?" present, size limit.
- request_integration: creates a row, dedupes, rejects a key-shaped string, alert recorded; list tool filters by status; resolve via command updates it.
- Console Requests tab renders rows from fixtures and resolving enqueues `resolve_integration_request` (e2e on `FARM_E2E_PORT`).
- Skill frontmatter valid; `farm connect claude-code --skill` prints the path and does not write without `--write` + confirmation.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`; `pnpm --prefix console check` + e2e green. Reply ≤ 10 lines.
