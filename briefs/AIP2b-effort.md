# Brief AIP2b — per-task reasoning effort for AI workers

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push. Never read `.env`.
Run every command in the foreground and wait for it; never start one in the background and poll it. Report once.

## Goal (owner, 2026-10-05)
The calling AI must be able to set **model and effort per task**, e.g. start two Claude workers in parallel, one on `opus` at effort `high`, one on
`sonnet` at effort `low`. Model already flows end to end; effort does not exist yet.

## Facts (verified by the lead)
- Claude Code 2.1.268: `--effort <level>` with levels `low, medium, high, xhigh, max`; `--model <model>`.
- The model path today: `farm/gateway/ai_tools.py` (`ai_start`, `ai_start_many`, `ai_reply`) → `AiJobSpec.model` (`farm/capabilities/schemas.py`)
  → `ai_jobs.model` column → `farm/ai/jobs.py` builds the `ask_ai` params (`("model", job.model)`) → `AskAiIn.model` → driver argv
  (`farm/executors/cli_agent/claude.py`: `argv.extend(["--model", model])`).

## Owns
`farm/capabilities/schemas.py` (AI section), `farm/ai/jobs.py`, `farm/gateway/ai_tools.py`, `farm/executors/cli_agent/{claude,codex,base}.py`,
one new migration `farm/db/migrations/versions/0011_ai_job_effort.py`, tests `tests/test_ai_effort.py` (+ minimal updates to existing AI tests).

## Build
1. `AiEffort = Literal["low", "medium", "high", "xhigh", "max"]`; optional `effort` on `AskAiIn`, `AiJobSpec`, `AiStarted`/status/result models.
2. Migration 0011: `ai_jobs.effort text null` with a check constraint on the five values; reversible.
3. Jobs persist `effort`, pass it in the `ask_ai` params, and `ai_reply` carries it over like `model` (overridable per reply).
4. Tools: `ai_start`, `ai_start_many` (per job), `ai_reply` and the blocking `ask_ai` accept `effort` with a clear description.
5. Drivers: Claude → `--effort <level>`. Codex → `-c model_reasoning_effort=<low|medium|high>` (map `xhigh`/`max` → `high`). agy/Hermes → ignore,
   and say so in the result (`effort_applied: false`). Validate against the allow-list in the driver too (argv safety, as SEC1).
6. `list_ais` shows per account which efforts and models are supported.

## Acceptance tests (fake-CLI harness)
- `ai_start_many` with two Claude jobs: (opus, high) and (sonnet, low) → fake CLI receives `--model opus --effort high` and `--model sonnet --effort low`;
  both persisted and reported back.
- Invalid effort rejected before any process starts. Codex mapping + agy ignore tested. `ai_reply` carries effort over. Existing AI tests pass.

## Done when
`powershell -File scripts/check.ps1` → `RESULT: all passed`. Reply ≤ 10 lines.
