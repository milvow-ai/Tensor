# Hermes Maximum Capability Integration

Status: 2026-10-03. This document extends `docs/TENSOR-FOUNDATION.md` and replaces its narrow "agent chores" role for Hermes.

## How this was researched

**Primary source:** the Hermes Agent repository (NousResearch, MIT), cloned at commit `c8301ea6` (2026-10-03, pinned in `research/library.tsv`). Tool and MCP findings: `research/h2-hermes-tools-mcp.md`. Core runtime facts were checked directly in source and docs:
- `website/docs/developer-guide/middleware.md`
- `user-guide/features/api-server.md`
- `guides/python-library.md`
- `hermes_cli/oneshot.py`
- `run_agent.py`
- `tools/delegation_output_schema.py`

Earlier context comes from `research/r6-ai-layer.md` and `.claude/skills/orchestrate/references/hermes.md`.

**Not finished:** two research workers (core runtime, external MCP ecosystem) were stopped to save tokens. What they would have confirmed is listed under §22 "Unverified". Treat those items as Phase 3 checks.

**Labels:**
- **[src]** = verified in source or docs.
- **[h2]** = from the tools research file.
- **[mine]** = my reasoning, not a published framework.

---

## The answer in one paragraph

Hermes becomes Tensor's **agent executor** for every task whose next step depends on what was just found: exploring a website's quote, booking or contact flows; finding where a decision-maker's address is published; multi-source company and market research; following up a referral; preparing a meeting brief. It is also the **development worker** that builds Tensor and the **operations worker** that runs briefings and diagnostics. It never touches money, quotas, compliance, sending or canonical state.

Tensor lets Hermes act only through a **Tensor MCP server** whose tools *are* the policy gates. Its model calls go through a **Tensor model gateway** that meters every token. It sits behind an `AgentExecutor` port with a second implementation (the Claude CLI), so Tensor never depends on Hermes. **[mine]**

---

## 1. Hermes capability inventory

| Area | Capability | Facts | Source |
|---|---|---|---|
| Agent loop | Plans, picks tools, retries, multi-step | `AIAgent` runs the full tool loop. `max_iterations` defaults to **unlimited**; `iteration_budget` and `run_budget_seconds` exist | [src] `run_agent.py:268,297`; `python-library.md` |
| Delegation | Sub-agents with an output contract | `delegate_task` takes an optional JSON Schema `output_schema`. The parent validates with jsonschema and allows **one** bounded retry | [src] `tools/delegation_output_schema.py` |
| Sessions, memory | Persistent sessions, memory, context compression | State DB, session search, memory providers, compression | [src] doc set: `session-storage.md`, `memory.md`, `context-compression-and-caching.md` |
| Scheduling | Cron, goals, loops, kanban worker lanes | Built-in scheduler and worker lanes | [src] docs present (`cron.md`, `kanban-worker-lanes.md`); details not deep-read |
| Interfaces | One-shot CLI `-z` with `--usage-file` | The usage report includes `estimated_cost_usd`, `cost_status`, input/output/cache/reasoning tokens, `api_calls`, `model`, `provider`, `session_id`, `completed`, `partial`, `interrupted`, `turn_exit_reason`, plus auxiliary totals | [src] `hermes_cli/oneshot.py:26-40` |
| Interfaces | Python library | `from run_agent import AIAgent`; `chat()`, `run_conversation()` → a dict with messages and metadata. `enabled_toolsets` / `disabled_toolsets`. No supported wheel, so install from source | [src] `python-library.md` |
| Interfaces | OpenAI-compatible API server | `/v1/chat/completions` (stateless) and `/v1/responses` (server-side state). Bearer `API_SERVER_KEY`, SSE streaming with tool-progress events, port 8642 | [src] `api-server.md` |
| Interfaces | MCP server mode | `mcp_serve.py` exposes Hermes sessions, events and **pending approvals** (`list_pending_approvals`, `respond_to_approval`, `poll_events`) to other MCP clients | [src] function names only |
| Interfaces | ACP, batch runner | Editor protocol; batch trajectories with checkpoint and resume | [src] docs present |
| Control | Middleware | `tool_request` rewrites arguments; `tool_execution` **wraps or replaces the actual tool call**; `llm_request` / `llm_execution` wrap model calls | [src] `middleware.md:9-60,105-121` |
| Control | Hooks, approvals, guardrails | Observer hooks (including "block directives"), approval checks, `smart` approvals by default, `--yolo` skips approvals | [src] `middleware.md:112`; [h2] |
| Control | Sandboxes | Terminal backends: `local` (**unsandboxed** by default; Git Bash on Windows), Docker (cap-drop ALL, optional egress firewall), SSH, Modal, Daytona and others | [h2] |
| Control | Egress controls, secrets, credential pools, profiles | Documented features | [src] docs present; not deep-read (see §22) |
| Browser | Real headless Chromium | JS rendering, clicking, typing, forms, screenshots, vision. Backends: Lightpanda, Camofox, CDP attach, Browserbase, Browser Use cloud, Firecrawl cloud | [h2] |
| Browser | Evidence retention | Screenshots are kept 24 h in `~/.hermes/cache/screenshots`. No raw HTML, HAR, headers or hashes. **No robots.txt handling** | [h2] |
| Web | Search and extract | Free backends: DDGS, SearXNG, Brave free tier. A keyless vendor fallback ring (Exa, Parallel, Keenable, Firecrawl) is **on by default** and sends queries off the machine. Extract needs Firecrawl, Tavily, Exa, Parallel, Keenable or Perplexity | [h2] |
| MCP client | Connects to many servers | stdio, Streamable HTTP, SSE; OAuth 2.1 PKCE and device code; static headers; include/exclude globs; BM25 tool search; resources and prompts; sampling and elicitation **on by default**; 300 s tool timeout; `trust: untrusted` forces approval on write tools | [h2] |
| Messaging | Gateway: Telegram, Slack, Teams, email and more; inbound webhooks; A2A | The email channel and `deliver: email` exist, which is a risk for Tensor | [h2] |
| Other | Code execution, document/PDF extraction, vision, computer use, LSP, skills, image generation, TTS, mixture-of-agents | Present | [src] docs list |
| Catalog | `optional-mcps/` | About 60 ready configs (Supabase, Notion, n8n, Indeed, Attio, Close, Calendly, Sentry, Neon, …) | [src] directory listing |

## 2. MCP / API / connector inventory: ways Hermes reaches the outside

| Path | What Hermes can use | Tensor production verdict [mine] |
|---|---|---|
| Native tools | Browser, web, vision, code, terminal, files | **Allowed in restricted form** per role (see §13) |
| MCP: Tensor's own server | Gated Tensor capabilities | **Primary path** for anything with cost or state |
| MCP: third-party | Apollo, Clay, GitHub, Supabase, Playwright, … | **Dev and ops roles only, or supervised use.** A third-party MCP spends quota outside Tensor's ledger |
| REST APIs | Through code execution or terminal | **Denied in production roles.** It would bypass adapters |
| OpenAI- or Anthropic-compatible model APIs | Its own LLM | **Through the Tensor model gateway** only (§12) |
| CLI tools, subprocess, Python | Terminal or code execution | **Dev role only** |
| Messaging gateway | Telegram and others | **Ops role:** outbound to the founder only. Email channel disabled |
| Inbound webhooks | Triggers | **Ops role:** e.g. a Tensor failure-event webhook |

**Architecture choice:** route through **Tensor → Hermes → Tensor MCP → adapter → API**, never Tensor → Hermes → raw API.
- An agent picks *which* capability to use. The adapter keeps idempotency, accounting and normalisation.
- Direct agent → API is acceptable only where Tensor has no adapter and the action is read-only and unmetered (dev and ops research).

## 3. Tensor capability mapping (all 50 capabilities in section B of the foundation doc)

Roles:
- **A** = Hermes owns the execution.
- **B** = Hermes executes; Tensor owns logic and state.
- **C** = Hermes assists.
- **D** = do not use Hermes.
- **E** = Hermes + an existing provider.
- **F** = a new capability unlocked by Hermes.

| # | Tensor capability | Hermes capability | Integration | Role | Why | Limits |
|---|---|---|---|---|---|---|
| 1 | Campaign config | Drafting from market evidence | MCP `submit.campaign_draft` | C | Drafting is useful; settings are a human choice | Human approves |
| 2 | ICP and offer definitions | Research synthesis | MCP submit | C | Same | Same |
| 3 | Pipeline definition | – | – | D | Deterministic graph; UI-edited | – |
| 4 | Policies | – | – | D | Must sit outside the agent | – |
| 5 | Secrets | – | – | D | Hermes never sees provider secrets | – |
| 6 | Canonical model | – | – | D | Tensor's asset | – |
| 7 | Entity resolution | Multi-source check of ambiguous pairs | MCP `facts.get` + submit | C | Only for ties; rules decide | Cost per pair |
| 8 | Fact store | Writes only through submit tools | MCP | D (store) | Validation and provenance in code | – |
| 9 | Evidence store | Requests captures | MCP `evidence.capture` | B (collection) / D (store) | Hermes's own screenshots are not durable evidence [h2] | Capture is deterministic |
| 10 | Freshness | – | – | D | Policy | – |
| 11 | Single-flight | – | – | D | Code guarantees idempotency | – |
| 12 | Capability catalog | Consumer only | MCP | D | – | – |
| 13 | Provider adapters | Clay through Clay MCP (no API at our plan) | Hermes + Clay MCP | E (Clay only) | The only automatable Clay path at $0 | Supervised; budget-capped |
| 14 | Resource registry | – | – | D | – | – |
| 15 | Metering | Usage file and gateway logs feed the ledger | Runner ingests | D | Exact accounting needs code | – |
| 16 | Routing | Weekly advisory report | Ops role | C | Suggestions only | Human decides |
| 17 | Rate limits, breakers | – | – | D | – | – |
| 18 | Information-need planning | Plans *within* a run | Brief | D (Tensor) / B (in-run) | Tensor decides what is needed; Hermes decides how to find it | – |
| 19 | Company discovery | Finds companies from public lists and directories for a hypothesis | MCP submit | B | Exploratory sourcing beats fixed adapters for niche verticals | Directory ToS; dedup in Tensor |
| 20 | Crawl and render | Exploratory navigation | Native browser | B | Evidence capture stays in Tensor | No robots handling [h2]: enforced in Tensor capture |
| 21 | Website audit (Offer A) | Explores IA, CTAs, forms, flows; proposes observations with evidence ids | Browser + MCP capture/submit | B | Its main strength | Measurements (PSI/CrUX) stay deterministic |
| 22 | Workflow signals (Offer B) | Explores inquiry, booking, quote, support and careers pages | Same | B | Same | Observation only |
| 23 | Business signals | Jobs, news, registry research | Web + MCP | B | Multi-source | Ads: human task (Meta UI ToS, r1) |
| 24 | Opportunity inference | Proposes hypotheses that point to observations | MCP submit (`kind: inferred`) | C | Tensor's model plus rules decide | Never stated as fact |
| 25 | Offer selection | Proposal | – | D (C: proposal) | Business logic | – |
| 26 | Qualification | – | – | D | Explainable scoring in code | – |
| 27 | Contact discovery | Multi-source decision-maker identification | MCP (`capability.request` → Apollo adapter) + web | B / E | Reasoning across sources | Apollo through Tensor's ledger only |
| 28 | Email find / verify | **Finds published addresses** with source URL; verification is not Hermes's job | MCP capture + submit; verify is an API call | B (find) / D (verify) | CASL needs publication evidence | – |
| 29 | Legal basis | Finds the evidence; Tensor decides | MCP | B (evidence) / D (decision) | The compliance gate stays in code | – |
| 30 | Personalisation | – | Single structured Claude call | D | No tools needed, so an agent loop is waste | – |
| 31 | Claim verification | – | Code + verifier model | D | Must be deterministic | – |
| 32 | Approval | Can observe the queue (ops) | – | D | A human gate | – |
| 33 | Reply classification | Referral follow-up research | MCP | D (classify) / F (follow-up) | – | – |
| 34 | Learning loop | Weekly analysis → `learning` proposals | Ops role, read-only MCP | C | Pattern-finding over outcomes | Human accepts |
| 35 | Market research | Sources, datasets, analysis | Market role | **A (execution)** | Its best fit; also fills research gap W8 | Tensor stores and scores |
| 36–39 | Email send, schedule, bounces, replies | – | – | **D (forbidden)** | Hermes has email paths [h2]; all are disabled | – |
| 40 | CRM state | Pre-call brief | Market or research role | F | New | – |
| 41–44 | Workflows, workers, DB, object storage | – | – | D | Platform | – |
| 45 | Observability | Summarises logs and traces; Hermes trajectories ingested as provenance | Ops role, read-only | C | – | – |
| 46 | Dashboard | Builds it (dev role) | Dev | C (dev) | – | Code review by worker-check |
| 47 | Notifications | Daily brief and ops digest over Telegram | Ops role (cron + gateway) | A (digest) | Built in | Alerts stay a direct Telegram call |
| 48 | Packaging, deploy | Assists | Dev or ops role | C | – | Human-approved |
| 49 | Model gateway | Consumer | – | D | – | – |
| 50 | Evals | Batch runner for agent-run evals | Batch | C | – | Small sets |

**New capabilities Hermes unlocks (F)**:
1. Interactive flow exploration with captured evidence.
2. Published-address discovery for CASL.
3. Market evidence and dataset collection.
4. Pre-call briefs.
5. Referral follow-up.
6. An ops briefing autopilot.
7. Cheaper development throughput.
8. Clay at $0, supervised.
9. **"Agent discovers, code productises":** repeated successful run patterns are turned into deterministic extractors, so cost falls over time **[mine]**.

## 4. Hermes vs Tensor: the responsibility boundary

| Tensor Intelligence (owns) | Hermes (executes) |
|---|---|
| What to research and why; required facts; freshness | *How* to find it within a bounded brief |
| Canonical facts, evidence store, provenance | Proposing facts and observations that cite evidence ids |
| Opportunity acceptance, offer selection, qualification, scores | Proposing hypotheses, marked `inferred` |
| Budgets, quotas, routing, provider policy | Spending only what its run budget allows, through Tensor tools |
| Legal basis, suppression, approvals, sending | Nothing; no path exists |
| Pipeline state, retries, idempotency (DBOS) | One run = one idempotent task (`run_id` = request hash) |

**Why:**
- Everything on the left must be deterministic, auditable and identical whichever executor is used.
- Everything on the right benefits from adaptive tool use.
- An agent's output is a proposal until Tensor's validators accept it. **[mine]**

## 5. Hermes vs deterministic code

**Use Hermes when at least one of these holds** **[mine]**:
- the next step depends on what the previous step found (unknown site structure, multi-hop navigation);
- at least three heterogeneous sources must be reconciled;
- interaction is required (clicks, forms, flows);
- the task is rare or one-off, where writing a brief is cheaper than writing code (dataset pulls, dev and ops chores);
- the target is a prospect that has passed the cheap filters and still lacks evidence.

**Do not use Hermes when:**
- one known API call answers it;
- it is a transformation, normalisation, scoring or SQL query;
- exact accounting, a transaction or idempotency is needed;
- it is a compliance decision;
- it is a single LLM call with fixed context (email writing, reply classification);
- it runs more than about 100 times a day at low value per run (cost). Productise it after the agent has shown the pattern.

## 6. Hermes vs provider adapters

- **Metered APIs with an adapter** (Apollo, Reoon, PSI, Adzuna): **always through the adapter**, reached by Hermes only as `capability.request` over Tensor MCP.
  - **Why:** quota reservation, dedup, normalisation, failure charging.
  - **Do not** attach the Apollo MCP to production roles. It would spend credits outside Tensor's ledger.
- **Providers without an API at our plan** (Clay at $0): Hermes + the provider's MCP in a **supervised `clay-assist` role**.
  - The brief caps runs per day; results go back through submit tools; credit use is reconciled by hand.
  - Clay MCP compatibility with non-claude.ai clients is UNVERIFIED (§22).
- **Read-only, unmetered sources** (public web, docs): Hermes's native tools.

## 7. Research-agent architecture (prospect dossier)

```
L0 deterministic (cheap, every prospect): fixed-page crawl (home, about, contact, careers, services)
   · PSI/CrUX · wappalyzergo · ATS jobs · Apollo person search
L1 single LLM calls: page → facts extraction (small model)
Coverage check (code): required facts present? ≥ 2 citable observations? decision maker found?
   ├─ yes → qualify
   └─ no, and fit score ≥ escalation threshold → L2 Hermes research run
L2 Hermes (role "research"), bounded: max iterations, seconds, model tier, token budget
   tools: browser (explore), web search (SearXNG/DDGS), Tensor MCP:
          facts.get · evidence.capture · capability.request · submit.dossier
   → structured submission → Tensor validators → facts / observations / proposed opportunities
```

- **Context** is held inside a single run (session). Across runs it comes from Tensor's `facts.get`, *not* Hermes memory: memory is disabled in production roles, so knowledge lives only in Tensor.
- **Escalation triggers:** missing required facts; an interactive flow to inspect; Canadian contacts needing published-address evidence; an ambiguous entity; a referral reply. **[mine]**

## 8. Website-agent architecture

- **Hermes explores:** navigation, CTAs, forms, booking, quote and contact flows, pricing, case studies, careers.
- **Tensor captures:**
  - Every observation Hermes wants to cite must reference an `evidence_id` returned by `evidence.capture(url, steps[])`.
  - That is a Tensor Playwright service: screenshot (fold and full page), DOM, headers, final URL, UTC time, viewport, SHA-256, with robots.txt and RFC 9309 enforced.
  - `steps[]` replays the clicks and fills Hermes discovered, so multi-step states are reproducible.
- **Why not Hermes's own browser as evidence:** screenshots are deleted after 24 h, and it keeps no HTML, headers, hashes or robots handling [h2].
- **So a separate browser *service* remains; a separate browser *agent* does not.** Exploration logic is Hermes's.
- **Measured facts** (PSI/CrUX) stay deterministic.
- **The r5 citable / not-citable rules** are enforced by the claim verifier.

## 9. AI-workflow opportunity research

Chain: `OBSERVATION (Hermes proposes, Tensor validates against evidence) → EVIDENCE (Tensor-captured) → OPPORTUNITY HYPOTHESIS (Hermes may propose, kind=inferred; Tensor's T3 model + rules accept, edit or drop)`.

Hermes sits at observation discovery and hypothesis *proposal*. It never sits at acceptance.

## 10. Market-research architecture

```
market_hypothesis → Hermes role "market" (web, document extraction, sandboxed code execution, dataset downloads)
   → submit.market_evidence {source, publisher, date, finding, proposed GRADE, conflict of interest}
   → Tensor hypothesis store (versioned) → deterministic weighted-sum scoring with coverage → human review
```

**First real job, before any Tensor code exists:** on the founder's PC (open network), pull BTOS, BLS OEWS, Census SUSB/CBP and StatCan business counts, and compute the r2 criteria tables. That closes gap W8. Outputs are checked against the source files.

## 11. Development-agent architecture

- **Role `dev`:** terminal, files, git, tests, Docker, on a local backend in a git worktree; the GitHub MCP or skill is read-only.
- **Model:** Qwen3-Coder-Next or GLM-5 on Bedrock, per the user's setup.
- **Process:** it works under the orchestrate skill as a *cheap builder for exact mechanical steps*.
  - Claude (lead) plans and reviews.
  - worker-check verifies.
  - **Hermes never commits or pushes** (`hermes.md` rules).
- **Good fit:** adapters from a spec, migrations, dashboard components from a design, test scaffolding, doc lookup, error diagnosis.
- **Poor fit:** architecture decisions and taste work. `hermes.md` itself warns that Qwen "decides badly".
- **Optional:** `mcp_serve.py` lets Claude Code watch Hermes sessions and answer its approvals.

## 12. Operations-agent architecture

- **Role `ops`:** cron + Telegram gateway + read-only Tensor MCP tools: `ops.funnel`, `ops.failures`, `ops.provider_health`, `ops.logs_tail`, `ops.backup_status`.

| Automation level | Actions |
|---|---|
| **Fully automated** | Daily brief; failed-workflow triage summary; provider-health digest; backup-freshness check; dependency-update report |
| **Human-approved** (proposal in the dashboard) | Retry a failed workflow; pause a connection; open a dependency-update PR; restart a container; VPS maintenance |
| **Forbidden** | Sending or queuing prospect email; editing suppression, legal-basis, quota, budget or routing config; deleting data; migrations on production; credential changes; unapproved deploys |

## 13. Resource Manager integration

**Decision: Option D (A + C), never B** **[mine]**.

- **A — Hermes is an executor.** Every Hermes run is a `CapabilityRequest` for an agentic capability (`research.dossier`, `web.explore`, `contact.find_published`, `market.research`) on connection `hermes@<host>`. Its consumption model counts **model tokens and cost**, plus any paid web or browser units.
- **Nested accounting.** Every Tensor MCP call inside a run carries `parent_request_id`. Child capability calls (an Apollo lookup, a capture) are charged to the run's budget, and the run is refused when the budget is exhausted.
- **C — Hermes advises.** The ops role writes routing and health proposals to `learning`.
- **B rejected.** Quota accounting, reservations and failover must be exact and identical for every executor; an agent cannot guarantee that.

## 14. Security and policy architecture (Hermes cannot bypass Tensor)

Five layers. Layers 1, 2 and 5 do not depend on Hermes behaving well. **[mine]**, applying least privilege (Saltzer & Schroeder, 1975).

1. **Capability exposure.**
   - Production roles hold **no** provider keys, no DB credentials, no SMTP/IMAP and no `EMAIL_*` variables.
   - Their only side-effect tools are Tensor MCP tools. Each tool is a CapabilityRequest, and Tensor checks budget, quota, campaign scope, suppression and legal basis **server-side** before executing.
   ```
   Hermes → tensor-mcp tool → Tensor policy gate → allowed? → no: structured refusal / yes: Resource Manager → executor
   ```
2. **Toolset restriction per role.**
   - Set `enabled_toolsets` (or the profile equivalent): research = browser + web + vision + `mcp:tensor`.
   - No terminal, files, code execution or messaging in production roles.
   - MCP include filters.
   - Sampling and elicitation off.
   - `web.keyless_fallback` and `web.keyless_rescue` set to false.
   - Mail skills (himalaya, google-workspace) removed.
3. **In-process middleware (defence in depth).**
   - A Tensor plugin uses `tool_execution` middleware to check every call against the run's allowlist and budget. It returns a refusal instead of calling `next_call` [src `middleware.md`].
   - `llm_execution` middleware does the same for the token budget.
4. **Containment.**
   - Runs execute in a `hermes-runner` container with Docker terminal backends and egress limits.
   - There is a wall-clock kill from outside (the runner), plus `run_budget_seconds` and `max_iterations` set explicitly (the default is unlimited).
5. **Model gateway.**
   - Hermes's provider is set to `custom`, with its base URL pointing at **Tensor's OpenAI-compatible model gateway**. The gateway meters every token against the run budget and the Resource Manager's quotas, then forwards to Bedrock, OpenRouter or Anthropic.
   - Exact accounting. The kill switch sits outside the agent.

**Prompt-injection stance.**
- Web pages are untrusted input (OWASP Top 10 for LLM Applications, LLM01 Prompt Injection).
- Production roles get untrusted content plus web egress, but **no private data** beyond the brief and public company facts, and **no external write channels**. That breaks the "lethal trifecta" (Willison, 2025: private data + untrusted content + external communication).

**Verdict: technically possible today.** Layers 1, 2 and 4 use documented features. Layer 3 uses the documented middleware contract. Layer 5 uses Hermes's custom-provider support (used by the founder today with `custom:bedrock-mantle`).

## 15. Structured-output architecture

1. Hermes does not enforce a schema on its final answer; only delegation does (`output_schema` + one retry) [src].
2. **So results arrive only through Tensor submit tools.**
   - Examples: `submit.dossier`, `submit.market_evidence`, `submit.contact_evidence`.
   - Each tool's MCP `inputSchema` is generated from Tensor's Pydantic models: `{observations[], facts[], opportunities[] (kind=inferred), sources[], confidence, gaps[]}`.
3. Tensor validates:
   - schema;
   - every `evidence_id` exists and belongs to this run;
   - quoted text matches the stored DOM or text;
   - inferred items are never typed as observations.
4. On failure the tool returns the errors. The agent gets **one** retry, mirroring Hermes's own delegation rule; after that the run fails, DBOS records it, and a human task is created.
5. **Provenance:** the run's session ID, usage report and trajectory file are stored as evidence on the run.
6. **The final prose answer is ignored.**

## 16. Cost analysis

**Software:** $0 (MIT).
**Infrastructure:** runs on the PC (Chromium + Python, about 1–2 GB RAM per concurrent run, an estimate [mine]); $0 until the VPS.

**Model cost** is where Hermes costs money. The overhead is about 13K tokens per call (`hermes.md`). Per-run estimates **[mine]**, with prices from r6:

| Run type | Model calls | Tokens (in / out) | Bedrock Qwen3-Coder-Next ($0.50/$1.20 per MTok) | Claude Sonnet 5.5 ($2/$10 per MTok, no cache) |
|---|---|---|---|---|
| Website exploration | 15–30 | about 150–300k / 8k | about $0.08–0.16 | about $0.38–0.68 |
| Deep dossier | 25–50 | about 250–500k / 15k | about $0.14–0.27 | about $0.65–1.15 |
| Market study | 40–100 | about 0.5–1.5M / 30k | about $0.29–0.79 | about $1.30–3.30 |

Compare a deterministic L0+L1 dossier at about $0–0.02.

**Rules:**
- Hermes runs only on prospects that survive the filters.
- Use the cheapest model that passes the golden set.
- Prompt caching (cache reads at 0.1× input price) cuts repeated prefixes.

**AWS new-account credits** ($100 + up to $100, 6 months, r6) would fund roughly 600–1,200 Qwen exploration runs, **if** the credits apply to these models (UNVERIFIED).

| Hermes decreases | Hermes increases |
|---|---|
| Engineering for site-specific exploration | Tokens per prospect (10–100× a deterministic pass) |
| Integration work for one-off and MCP-available tasks | Latency: minutes, not seconds |
| Ops tooling (cron, briefings, notifications) | Failure probability and nondeterminism |
| Market-research effort | Debugging complexity (agent traces) |
| Dev time on mechanical work | Prompt-injection exposure |

## 17. Reliability analysis

| Failure mode | Mitigation |
|---|---|
| Loops and runaway turns (`max_iterations` unlimited by default) | Explicit iteration and time budgets; external kill; gateway token cap |
| Malformed or partial output | Submit-tool validation + one retry; `completed` / `partial` / `turn_exit_reason` in the usage report |
| Hallucinated evidence | Evidence only via `evidence.capture`; quote matching |
| An MCP OAuth refresh dies, so the server is parked until `hermes mcp login` [h2] | Production roles use only the local Tensor MCP (no OAuth); third-party OAuth only in supervised roles |
| Docs and code drift (two mismatches found [h2]) | Pin the commit; integration tests on each upgrade |
| State DB contention across parallel runs | One HERMES_HOME per runner slot |
| Upstream breaking changes in a young project (first release 2026-05-14, r6) | Pin; AgentExecutor contract tests; Claude CLI as the alternative executor |

## 18. Before → After

| Area | Before (foundation doc) | After |
|---|---|---|
| Agent executor | Claude CLI behind `llm.reason`; Hermes for chores | `AgentExecutor` port: **Hermes primary**, Claude CLI alternative |
| Website research | Custom crawl + rubric + planned exploration logic | Fixed-page L0 crawl + **Hermes exploration** + Tensor capture service |
| Contact discovery for Canada | Undesigned multi-source search | **Hermes `contact.find_published`** + capture evidence |
| Market research | Ingestion scripts to write | **Hermes market role**; Tensor stores and scores |
| Clay at $0 | Agent or human executor, undesigned | **Hermes + Clay MCP**, supervised |
| Ops | Health and digest jobs to write | **Hermes ops role** (cron + Telegram + read-only MCP) |
| Development | Claude Code + subagents | + **Hermes dev role** for mechanical builds under orchestrate |
| Model metering | Thin router for Tensor's own calls | + **OpenAI-compatible gateway** that Hermes uses (metered) |
| Agent interface | Ad-hoc | **Tensor MCP server** (gated tools + submit tools) |

## 19. What can be removed or simplified

- **Removed:**
  - Writing any custom agent or tool loop.
  - A web-search adapter for research (Hermes's free backends are used inside runs).
  - A custom ops digest and scheduler for briefings.
  - Market-dataset ingestion scripts (replaced by validated Hermes runs).
- **Simplified:**
  - The deterministic crawler shrinks to a fixed page set; exploration moves to Hermes.
  - The notification code shrinks to one direct alert call; digests move to Hermes.
- **Replaced by MCP:** dev-time access to GitHub, docs and Supabase (dev role).

## 20. What must remain in Tensor

- Canonical model, fact and evidence stores, and freshness.
- Resource Manager, quota ledger and capability catalog.
- Deterministic adapters for metered APIs.
- **Evidence capture service.**
- Legal-basis, suppression and approval gates.
- Qualification and offer selection.
- Personalisation (a single structured Claude call) and claim verification.
- Sending and inbound mail.
- DBOS pipeline and dashboard.
- **New:** the Tensor MCP server, the model gateway, and the `agent_run` table:
  - `agent_run`: run_id, role, executor, profile_version, budget, status, usage, trajectory_ref, parent_request_id.

## 21. Final recommended architecture

```
Dashboard (SPA) ── Tensor API ── Intelligence Engine (pipeline interpreter on DBOS)
                        │                 │
                        │          Knowledge layer: facts · evidence · capability_request ledger · agent_run
                        │                 │
                 Tensor MCP server ◄──────┤   (gated tools: facts.get · evidence.capture · capability.request ·
                 (policy gate)            │    submit.* · ops.read_*)
                        ▲                 ▼
                        │          Capability layer → Resource Manager (registry · quotas · budgets ·
                        │                                               routing · health · nested accounting)
                        │                 │
                        │          Executors
                        │           ├─ api      (Apollo, Reoon, PSI, Adzuna, …)
                        │           ├─ local    (Playwright capture service, wappalyzergo, Ollama)
                        │           ├─ llm      (thin router: single structured calls)
                        └───────────├─ agent    AgentExecutor ── HermesExecutor (hermes-runner container,
                                    │                            role profiles, model via Tensor gateway)
                                    │                         └─ ClaudeCliExecutor (alternative)
                                    └─ human    (dashboard tasks)
Tensor model gateway (OpenAI-compatible, metered) → Bedrock (mantle) · OpenRouter · Anthropic · Groq · Ollama
Outreach (EmailProvider) — unreachable from any agent role
Side roles outside the production path: Hermes dev (builds Tensor under orchestrate) · Hermes ops (cron, Telegram, read-only MCP)
```

**HermesExecutor invocation:**
- **Stage 0 (PC):** a CLI subprocess per run. `hermes -z <brief>` with role profile and `--usage-file`, inside the `hermes-runner` container; one HERMES_HOME per slot.
- **Stage 1+ (VPS):** the same, or the **API server** (`/v1/responses`, bearer key, internal network only) when warm processes are needed.
- The contract does not change.

## 22. Hermes limitations (and what Tensor still builds)

- **Evidence:** no durable evidence (24 h screenshots; no HTML, HAR, hashes) and no robots.txt handling [h2]. **Tensor builds the capture service.**
- **Defaults are permissive and must be overridden:**
  - unlimited iterations;
  - local terminal unsandboxed;
  - keyless web fallbacks send queries to vendors;
  - sampling and elicitation on;
  - email delivery paths exist (gateway, `deliver: email`, `hermes send`, mail skills, terminal) [h2].
- **No schema-enforced final answer.** Tensor builds the submit tools.
- **Cost is an estimate.** `estimated_cost_usd` carries `cost_status` / `cost_source`; exact accounting needs **Tensor's gateway**.
- **MCP:**
  - an OAuth refresh failure needs a human re-login;
  - no roots support;
  - 300 s tool timeout;
  - a third-party MCP bypasses Tensor's ledger.
- **Packaging and maturity:**
  - no supported wheel (install from the pinned source);
  - very large codebase (2,389 non-test Python files) and a young project, so upgrade risk;
  - documentation drift.
- **Runtime:**
  - concurrency is bounded by RAM (browser) and the shared state DB (one home per slot);
  - latency is minutes per run;
  - Windows status of some browser backends is unverified [h2].
- **Not good at:** taste and architecture decisions (`hermes.md`), compliance decisions, exact accounting, single-call tasks.

**Unverified (stopped research; Phase 3 checks):**
- the exact CLI and profile keys for iteration and time budgets and toolsets;
- egress / network-isolation configuration;
- API server concurrency limits;
- credential pools;
- details of cron and kanban lanes;
- `mcp_serve.py` tool list beyond function names;
- server-by-server autonomy of the third-party MCPs (Apollo, Clay with non-claude.ai clients, Zoho, Playwright MCP trace flags);
- whether AWS credits cover Bedrock models.

## 23. Phase-by-phase plan

| Phase | Hermes work | Tensor work | Done when |
|---|---|---|---|
| **P0 (now, PC, no Tensor code)** | Create role profiles: research, market, dev, ops, clay-assist. Apply §14 layer-2 settings. **Run the market role** to pull BTOS, OEWS, SUSB/CBP and StatCan and compute r2's criteria tables | – | Datasets and tables checked against the source files; gap W8 closed |
| **M1** | Dev role builds mechanical pieces under orchestrate | Tensor MCP server (facts.get, evidence.capture, submit.*), capture service, AgentExecutor + HermesExecutor (CLI), `agent_run`, usage ingestion | One prospect researched end to end with validated evidence |
| **M2** | Research role escalation: website exploration, `contact.find_published` | Coverage check, escalation thresholds, validators; golden set comparing deterministic, Hermes and Claude CLI | Hermes beats L0+L1 on evidence coverage at a measured cost |
| **M3** | Ops role: cron brief, failure triage. clay-assist supervised | `tool_execution` / `llm_execution` middleware plugin; model gateway metering; human-approval flow for ops proposals | The budget kill switch is tested; no tool outside the allowlist runs |
| **M4 (VPS)** | Several runner slots; optional API-server mode | Same Compose file; scale the `hermes-runner` replicas | No code change, only config |

### Mine, not published

- the role split;
- the escalation ladder and its triggers;
- the five-layer gate;
- the submit-tool pattern;
- "agent discovers, code productises";
- the per-run cost estimates;
- the phase plan.

### Provenance

- Hermes facts: the repo at `c8301ea6` (doc paths and files cited inline), checked 2026-10-03. **High** for documented contracts.
- Tool and MCP behaviour: [h2], from source with file:line citations in that file.
- Prices: r6 (secondary for Bedrock and OpenRouter). Cost estimates: **[mine]**.
