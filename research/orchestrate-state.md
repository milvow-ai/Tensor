# Orchestrate state

## Current state
Effort: Tensor, Milvow's own GTM engine (find → qualify → segment → personalise → email → remember → SaaS-grade dashboard), $0 budget.
Phase 1 (COLLECT) in progress. Phase 2 (DESIGN) waits for the user's answers to the Phase 1 questions. Phase 3 (DEVELOP) waits for the design gate.
Next action: user asked to stop all work (2026-10-02). On return: get answers to the 9 questions, then decide with the user whether to rerun R3, R5–R8 (narrower briefs) before Phase 2.

## Active workers
| Worker | Agent | Model | Owns | Status | Output file |
|---|---|---|---|---|---|
| R1 LLM tiers | general-purpose | inherited | nothing | Done | written by lead to research/01-llm-providers.md |
| R2 data/enrichment | general-purpose | inherited | nothing | Done | written by lead to research/02-data-sources-and-enrichment.md |
| R3 sending/deliverability/benchmarks | general-purpose | inherited | nothing | Failed — ended with no report delivered; rerun needed | research/03 not written |
| R4 runtime/db/ui | general-purpose | inherited | nothing | Done | written by lead to research/04-runtime-db-orchestration.md |
| R5 OSS GTM systems | general-purpose | sonnet | research/05-oss-gtm-systems.md | Stopped on user request 2026-10-02, no file written | – |
| R6 dashboard | general-purpose | sonnet | research/06-dashboard.md | Stopped on user request 2026-10-02, no file written | – |
| R7 Hermes + MCP | general-purpose | sonnet | research/07-hermes-and-mcp.md | Stopped on user request 2026-10-02, no file written | – |
| R8 data assets | general-purpose | sonnet | research/08-data-assets.md, research/data/** | Stopped on user request 2026-10-02; partial downloads only (gpts-are-gpts, anthropic-economic-index), no README or checksums | research/data/ |

## Contract
- Phase gate: no product code until the user approves the Phase 2 design. Phase 1 output is research only.
- $0 budget. No sends, no purchases, no cloud resources created, no DNS changes without the user's explicit word.
- Never handle the user's keys or passwords. Hermes runs only with a budget the user sets.
- Evidence discipline: market facts need a source checked this session; mark UNVERIFIED otherwise.
- Lead lives in this cloud container: network reaches only GitHub, PyPI, npm (+ MCP connectors server-side). Hermes is not installed here.

## Decisions log
- 2026-10-02: Stopped building after the user's correction; schema moved to research/sketches/data-model-v0.sql as a sketch, not a decision — user asked for collect → design → develop.
- 2026-10-02: Research workers on sonnet per the skill's role table — token economics.
- 2026-10-02: Orchestrate pack copied unchanged into .claude/ so future cloud sessions have /orchestrate — cloud containers do not keep ~/.claude.
- 2026-10-02: n8n VPS (177.7.33.91) treated as unavailable — user says it is expired.

## Open questions for the user (asked 2026-10-02, Phase 1)
1. Hermes: on the user's Windows PC? Mode: (a) run Phase 3 from Claude Code on the PC, (b) lead writes briefs the user runs locally, (c) skip. Spend limit.
2. Zoho Mail plan and which mailboxes exist.
3. Claude plan (Pro / Max 5x / Max 20x).
4. Runtime: GitHub Actions OK? Card for free-tier sign-ups? An always-on PC?
5. milvow.com case studies (Cigna, Aetna, ConnexAI): real or placeholders?
6. First target countries.
7. Widen this cloud environment's network access, or keep it locked?
8. GitHub push blocked (403): user to install the Claude GitHub App on milvow-ai/Tensor. Local commit 1a82601 not pushed yet.
9. Manual LinkedIn touches (10–20/day) if Tensor queues them?
