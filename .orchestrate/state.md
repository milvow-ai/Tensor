# Orchestrate state

## Current state
Effort: Harness Farm + Farm Console (see HANDOFF.md). Phase: **0 — pre-setup, §8.1 questions asked (2026-10-03)**.
Next action: user answers §8.1 (open: 3–11); then a lead session **on the PC** runs §8.2, writes the plan here, waits for "go".

## Phase 0 findings (verified 2026-10-03, cloud session)
- Repo restored from bundle `3b4b1ec` onto branch `claude/zen-noether-qa7d3y`; zip == bundle tree.
- §8.1 Q1 GitHub: first push → HTTP 403; user installed the Claude GitHub App → push OK. Full history now on `origin/claude/zen-noether-qa7d3y` (first branch, so GitHub's default). **Resolved.**
- §8.1 Q2: this session is a cloud container and cannot reach the PC. §8.2 and Phase 1 need a PC session (`claude remote-control` in the repo folder, or Claude Desktop).
- Supabase: one org ("milvow-ai's Org"); 3 projects, all INACTIVE: APTIX (ap-southeast-2), milvow-dev (ap-south-1), Milvow_bsp (ap-south-1). 0 of 2 free active slots used → a new `harness-farm` project fits.
- Vercel: one team, "milvowai-5995's projects" (slug `milvow`); plan not checked (name suggests personal Hobby).
- Connectors on the cloud session: GitHub (read), Supabase, Vercel, Clay (one workspace; untouched, no credits spent), Notion, Gmail, Google Drive/Calendar, Figma, Firecrawl, Namecheap. **No mem0** → memory import still pending. Cloudflare connector needs authorization.

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
