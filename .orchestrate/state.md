# Orchestrate state

## Current state
Effort: Harness Farm + Farm Console (see HANDOFF.md). Phase: **0 — pre-setup, not started**.
Next action: new lead session reads CLAUDE.md + HANDOFF.md, asks the user the §8.1 questions, runs §8.2 checks, writes the plan here, waits for "go".

## Active workers
| Worker | Agent | Model | Owns | Status | Output file |
|---|---|---|---|---|---|
| (none yet) | | | | | |

## Contract
- Design decisions in HANDOFF.md §3–§5 are fixed; do not re-design.
- No spend, no account creation, no building before the user's "go".
- Keys only in the user's local .env; agents never see them.
- One writer per file; builders never commit; lead commits after checks pass.

## Decisions log
- 2026-10-03: Build order = Harness Farm → Farm Console → (later) Tensor — user decision.
- 2026-10-03: Stack = FastMCP 4 + DBOS + Postgres (Supabase) + Crawl4AI + Bifrost; Console = Next.js + shadcn starter on Vercel, controlled via Supabase command queue — previous session design.
- 2026-10-03: Builders = Gemini 3.8 (Antigravity CLI) bulk, Qwen via Hermes mechanical, Sonnet checks — user decision for token efficiency.
- 2026-10-03: Multi-account pools (e.g. 7 paid Clay accounts) are in scope; user owns them — user decision.

## Previous efforts
See .orchestrate/history-2026-10-02-03.md (research + design rounds).
