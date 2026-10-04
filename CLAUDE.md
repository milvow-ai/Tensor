# Tensor / Harness Farm — boot context

**Phase 1 is in progress: read the `▶ RESUME HERE` block at the top of `.orchestrate/state.md` first** — it is the handoff (status, what is in flight, next queue, token rules). Read `HANDOFF.md` sections only when you need them, not in full.

## What this repo is

- **Harness Farm** is one MCP gateway through which Claude and Hermes reach many tools, APIs, MCPs, agents and accounts. It provides capability routing, multi-account pools, quotas, billing control, a fact cache, evidence and run history.
- **Farm Console** is its dashboard, deployed on Vercel.
- **Tensor** (Milvow's GTM system) is built later on top of the Farm. Its design is in `docs/` and `research/`.

## Hard rules

1. **Phases are gated.**
   - Phase 0 is pre-setup: ask the user, set up the environment, plan. No building and no spending in Phase 0.
   - Phase 1 builds the Farm. Phase 2 builds the Console.
   - Show the plan and wait for the user's "go" before Phase 1.
2. **Token economy first.**
   - Claude (lead) plans, writes briefs, reviews diffs and commits.
   - Bulk code goes to Gemini 3.8 via the Antigravity/Gemini CLI. Mechanical steps go to Qwen via Hermes. Checks run as scripts, then worker-check (Sonnet).
   - No bulk file reading in the lead. Do not spawn research agents unless the user asks.
3. **Ask before spending:** money, paid credits, or new accounts.
4. **Never handle the user's keys.** They live in the user's `.env` on their PC. Hermes and Gemini never see provider keys.
5. **Engineering focus.** The user owns the accounts they connect (paid). Do not lecture about provider rules. Implement what `HANDOFF.md` specifies.
6. **Write things down.** Use the orchestrate skill (`.claude/skills/orchestrate/`). Keep the state file at `.orchestrate/state.md` **inside the repo**, because cloud containers lose `~/.claude`.
7. **Commit after every verified round.** Push to the session's designated branch.

## File map

| Path | Holds |
|---|---|
| `HANDOFF.md` | Plan, phases, checklists, build map, open-source materials |
| `docs/TENSOR-FOUNDATION.md` | Tensor problem, capabilities and architecture (A–H) |
| `docs/HERMES-INTEGRATION.md` | Hermes role, policy gate, limitations |
| `research/` | Verified research, dated 2026-10-02/03. `r1`–`r6` and `h2` hold provider limits, prices and terms |
| `research/library.tsv` + `scripts/fetch-library.sh` | Pinned open-source reference repos (clone into git-ignored `library/`) |
| `research/sketches/data-model-v0.sql` | Tested schema sketch (includes the `consume_quota()` reservation function) |
| `memory/mem0-import.jsonl` | 41 memories of decisions and preferences, ready to import into mem0 |
