# Orchestrate state

## Current state
Effort: Harness Farm + Farm Console (see HANDOFF.md). Phase: **0 — pre-setup, §8.1 questions asked 2026-10-04, waiting for answers**.
Next action: after the user answers, run §8.2 checks, record CLI flags below, write the plan, wait for "go".

## Phase 0 findings (2026-10-04, PC: Windows 11, 15.3 GB RAM)
- Repo cloned from bundle to `C:\Users\Amaan\Harness Farm\Tensor`. Push as GitHub user `Mr-amaanx` → 403 (no write access to milvow-ai/Tensor).
- Disk: C: 4.3 GB free (too little), D: 95.9 GB free.
- Present: git, python 3.12, uv (from hermes\bin), node, pnpm, docker, wsl, hermes (HERMES_HOME=%LOCALAPPDATA%\hermes), claude, **agy** (Antigravity CLI binary). Missing: gemini CLI, supabase CLI, go.
- mem0: 41 memories from memory/mem0-import.jsonl imported (infer=false) + 1 new constraint memory.

## Verified CLI flags
(filled in §8.2)

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
- 2026-10-04: Claude plan = Pro, weekly limit (53% left today) → lead stays lean — user.
- 2026-10-04: Gemini = Antigravity CLI (`agy`), separate Google account with Gemini Pro — user.
- 2026-10-04: **No AWS Bedrock** (AWS warning pending). Hermes uses OpenRouter free models; a very cheap paid model (DeepSeek-class) only for important steps; OpenRouter balance $3 total — user. Replaces §2/§7 "Qwen3-Coder-Next via Bedrock".

## Previous efforts
See .orchestrate/history-2026-10-02-03.md (research + design rounds).
