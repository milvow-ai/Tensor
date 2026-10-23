# Harness Farm + Codex setup and budget recovery

**Updated:** 2026-10-15 17:44 IST (Asia/Kolkata)

## What changed

- Added global Codex instructions at `C:\Users\Amaan\.codex\AGENTS.md`: when the user says “Use Harness Farm,” infer the active project directory, select the relevant AI worker pool, pass `cwd` when project access is needed, and return the worker's actual result. Don't ask the user to repeat an already-known repo or folder.
- Registered the global Codex MCP server `harness-farm` at `http://127.0.0.1:8787/mcp`, authenticated by the `FARM_TOKEN` environment variable. Codex CLI reports the server enabled. Restart Codex to load the new connection in existing sessions.
- Migrated the local Farm database and started `farm.exe run` (PID 122440). Health check returned HTTP 200.
- On 2026-10-15 at 17:28 IST, syncing `config/registry.yaml` deleted 13 provider budget rows. Restored all 13 using the exact `before` values in Farm's `audit_events`: each is `$0/month`, `hard_stop=true`. The global budget remains `$0/month`, `hard_stop=true`.
- The budget-only restore inserted rows in `public.budgets`; it did not modify any AI account, login, or Claude connection rows. The `claude` provider budget was restored to its prior `$0/month`, `hard_stop=true` value.
- Restored provider refs: `reoon`, `zerobounce`, `apollo`, `hunter`, `pagespeed`, `adzuna`, `ats_public`, `llm`, `clay`, `claude`, `gemini`, `codex`, `hermes` — each `$0/month`, `hard_stop=true`.
- Both registry files include valid budget entries for the five listed providers with prior budget rows: `clay`, `claude`, `gemini`, `codex`, and `hermes`.
- The earlier sync also changed Claude connection rows from the registry: it created `claude-01` as active; changed `claude-02` from `needs_login` to `active` and added its configured edit/parallel metadata; changed `claude-03` from `needs_login` to `paused`; and changed `claude-04` from `needs_login` to `active` and added its configured edit/parallel metadata. These were sync side effects, not part of the budget restore; they remain as the registry specifies.

## Important registry detail

Eight restored budget refs (`reoon`, `zerobounce`, `apollo`, `hunter`, `pagespeed`, `adzuna`, `ats_public`, `llm`) belong to provider rows retained in the database but absent from the current registry files. Registry validation rejects those refs when the provider is absent; a dry run confirms that syncing the current file would delete those eight budget rows again. Do not run a non-dry-run registry sync until those provider definitions are intentionally restored to the registry or the user approves dropping those budgets.

## How to invoke from any project

Say: **“Use Harness Farm for this project: have Claude review the current repo for [task].”**

The MCP server is `harness-farm`; relevant tools include `list_ais`, `ask_ai`, `ask_ai_batch`, `ai_start`, `ai_start_many`, `ai_wait`, and `ai_result`. The global instruction tells Codex to infer the active project's folder and pass it to a worker as `cwd` when needed.

## Current worker availability

- Claude workers: `claude-01`, `claude-02`, and `claude-04` active; `claude-03` paused.
- Gemini/Agy and Hermes have active accounts.
- Codex worker accounts need login.
