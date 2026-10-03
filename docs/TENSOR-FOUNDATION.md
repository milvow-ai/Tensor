# Tensor — foundation: problem, capabilities, architecture, resources

Hermes's role is revised in `docs/HERMES-INTEGRATION.md` (2026-10-03). That supersedes the 'Agent chores' row in D and executor item 1 in F.

Status: sections A–C were written on 2026-10-03, *before* resource research, as the brief requires. Sections D–H come from that research.

Labels used throughout:
- **[mine]** marks my reasoning, not a published framework.
- Named patterns carry their author and year.
- Market facts come only from the dated research files in `research/`.

---

## A. Problem definition

**What Tensor must do, in one sentence.** Turn a market hypothesis ("Offer X fits companies like Y in the US/Canada") into a *small* set of prospects. For each one there must be a specific, verifiable, commercially relevant reason to write now. Tensor then sends that email, measures it, and learns. It does this while spending as little metered capacity as possible and never paying twice for something it already knows.

**The unit of output is a dossier, not a lead.** A dossier holds:
- the resolved company identity;
- facts with provenance;
- observations (seen, with evidence);
- inferred opportunities (hypotheses that point to the observations they rest on);
- the selected offer (A website, B AI workflow, both, or none);
- the decision maker;
- the legal basis to email that person;
- a draft in which every claim traces to evidence.

**Why this is hard (the engineering problems underneath).**

1. **The market is unknown.** Verticals are not chosen yet. Campaigns must be cheap, comparable experiments, each carrying its hypothesis and its measured outcome.
2. **Identity across sources.** Apollo, Clay, websites and registries name the same company and person differently. Without entity resolution, caching and dedup break, so the same company is paid for, or emailed, twice. Record linkage is a known problem (Fellegi & Sunter, 1969).
3. **Metered resources do not share a unit.** Credits, actions, requests, tokens, rows and subscription windows each have their own reset rules and failure-charging rules. Some accounts must not be multiplied (provider ToS). Routing therefore has to weigh capability, remaining capacity, cost, quality and health together.
4. **Facts expire at different speeds.** Employee count changes over months, active ads over days, a job post over weeks, email validity over months. So caching has to happen per fact, with a freshness rule per attribute. The vocabulary for this already exists in HTTP caching: `max-age` and stale-while-revalidate (RFC 9111, 2022; RFC 5861, 2010).
5. **Observation is not inference.** "Has a quote form" is a fact. "Would benefit from automated qualification" is a hypothesis. They must be stored apart, and only evidenced claims may reach an email.
6. **Precision beats recall.** One false or generic claim costs reputation, replies and spam complaints. A missed prospect costs almost nothing. The pipeline is therefore a filter cascade: cheap, discriminating checks first, and expensive research only on survivors **[mine]**.
7. **The legal basis is per recipient.** US cold email runs on an opt-out regime (CAN-SPAM). Canada's CASL requires consent; implied consent through "conspicuous publication" has to be *evidenced* (exact rule to be confirmed in research/r3). So compliance is itself a fact with evidence.
8. **Sending reputation is shared and slow to recover.** Volume is constrained by reputation, not by how many leads exist.
9. **It must evolve without a rewrite.** Providers, models, infrastructure and volume will change. The core contracts must not.

**What Tensor optimises (metrics)** **[mine]**:
- **Outcome:** positive replies per 100 sent, then meetings, then clients.
- **Quality gates:**
  - claim precision: the share of email claims verifiably true at send time, with 100% as the gate;
  - share of drafts approved without edits;
  - bounce rate and complaint rate.
- **Efficiency:** metered units per qualified prospect, fact-cache hit rate, model spend per dossier.
- **Learning:** performance by campaign, offer, vertical and angle, always shown with sample size.

**Not in scope now:** multi-tenant SaaS, team accounts beyond Milvow, high-volume sending, LinkedIn automation, and scraping that breaks a provider's terms.

**Assumptions to confirm.**
- A1: the founder's computer runs Windows. I infer this from the Hermes command in the orchestrate pack.
- A2: initial sending volume is tens per day.
- A3: which Apollo and Clay plans exist today. Clay is connected; Apollo is unknown.
- A4: the sending mailbox is on milvow.com via Zoho (research/00); the plan is unknown.

---

## B. Required capabilities (complete map)

Classification is my initial judgement **[mine]**, to be revised in F:
- **P (proprietary):** Milvow's differentiation; build it.
- **C (commodity):** a mature product or open-source project should do it.

| # | Capability | Must do | Layer | P/C |
|---|---|---|---|---|
| 1 | Campaign configuration | Market hypothesis, countries, industries, size, titles, offer, rules, depth, daily limits, approvals | Control | P (schema) / C (UI) |
| 2 | ICP and offer definitions | Versioned, reusable across campaigns | Control | P |
| 3 | Pipeline definition | Stages, order, per-stage config and gates; edited visually | Control | P (model) / C (editor) |
| 4 | Policies | Provider and model preferences, compliance, approval, send limits | Control | P |
| 5 | Secrets and connection references | Credentials stored outside config; referenced by id | Platform | C |
| 6 | Canonical data model | Stable IDs; independent of any provider's schema | Knowledge | P |
| 7 | Entity resolution and dedup | Company by registrable domain plus fuzzy fallback; person by email, LinkedIn URL or name+company | Knowledge | C (library) + P (rules) |
| 8 | Fact store | Field-level facts with provenance, confidence, observed/verified/expires timestamps | Knowledge | P |
| 9 | Evidence store | Page snapshots, screenshots, raw provider responses, quotes | Knowledge | C (storage) / P (model) |
| 10 | Freshness policy | Per-attribute TTL, fresh/stale/missing decision, refresh-on-demand | Knowledge | P |
| 11 | Request dedup / single-flight / idempotency | Identical concurrent requests produce one provider call | Execution | C |
| 12 | Capability catalog | Generic typed contracts: `company.search`, `person.search`, `company.enrich`, `person.enrich`, `email.find`, `email.verify`, `web.fetch`, `web.render`, `web.audit`, `tech.detect`, `ads.lookup`, `jobs.lookup`, `registry.lookup`, `llm.extract`, `llm.reason`, `llm.write`, … | Harness | P |
| 13 | Provider adapters (Harness Operator) | Translate, authenticate, execute, normalise, retry, report usage, keep the raw response | Harness | P (thin) |
| 14 | Resource registry | Providers, connections, capabilities, consumption models, priority, status | Harness | P |
| 15 | Usage metering and quota ledger | Provider-specific units, reset rules, whether failures are charged, reservations | Harness | P |
| 16 | Routing and failover | Choose a connection by capability, health, capacity, cost, quality, priority, policy | Harness | P |
| 17 | Rate limits, circuit breakers, cooldowns, retries | Per connection and per provider | Harness / Execution | C (engine) + P (policy) |
| 18 | Information-need planning | Which facts each decision needs, and how fresh | Intelligence | P |
| 19 | Company discovery | Source strategies per campaign | Intelligence | P (strategy) / C (sources) |
| 20 | Website crawl and render | Fetch, render JavaScript, screenshot, extract | Execution | C |
| 21 | Website experience audit (Offer A) | Positioning, messaging, CTA, conversion path, trust, mobile, performance, forms; each with evidence | Intelligence | P (rubric) + C (measurement tools) |
| 22 | Workflow-signal detection (Offer B) | Inquiry handling, scheduling, quoting, support, documents, hiring for admin roles; each with evidence | Intelligence | P |
| 23 | Business signals | Hiring, ads, tech stack, registries, growth, reviews | Intelligence | C (sources) |
| 24 | Opportunity inference | Observations → hypotheses with confidence and linked evidence | Intelligence | P |
| 25 | Offer selection | A, B, both or none, with reasons | Intelligence | P |
| 26 | Qualification / prospect-quality scoring | Explainable; every sub-score points to facts | Intelligence | P |
| 27 | Contact discovery | Decision maker for the selected offer | Harness (via providers) | C |
| 28 | Email find and verify | Find, then verify (syntax, MX, catch-all, mailbox) | Harness | C |
| 29 | Legal-basis determination | Per recipient and country, with evidence (e.g. where an address was published) | Intelligence | P |
| 30 | Personalisation | Grounded, short draft built from dossier evidence | Intelligence | P |
| 31 | Claim verification and style lint | Each claim → evidence check; banned phrases, length, one CTA | Intelligence | P |
| 32 | Human approval | Queue, inline edit, approve or reject with reason | Experience | P (flow) / C (UI parts) |
| 33 | Reply classification and next action | Interested / not now / no / referral / OOO / unsubscribe / bounce | Intelligence | P |
| 34 | Learning loop | Outcomes → hypothesis and config changes, proposed and human-accepted | Intelligence | P |
| 35 | Market research | Vertical selection inputs; hypothesis backlog | Intelligence | P (method) + C (data) |
| 36 | EmailProvider port | `send`, `check_status`, `handle_bounce`, `track_reply`, `suppress` | Outreach | P (port) / C (providers) |
| 37 | Mailbox and domain pool, schedule | Caps, ramp, send windows in the recipient's timezone | Outreach | P |
| 38 | Bounce, complaint, suppression | Global suppression stored by hash | Outreach | P |
| 39 | Reply ingestion and threading | IMAP, Gmail API or Graph; Message-ID threading | Outreach | C |
| 40 | CRM state | Stages, meetings, outcomes | Outreach | C or P (to decide) |
| 41 | Durable workflows and queues | Retries, idempotency, schedules, concurrency, priority | Execution | C |
| 42 | Worker classes | General, browser, LLM; scale by adding replicas | Execution | C |
| 43 | Database, migrations, backups | System of record | Platform | C |
| 44 | Object storage | Snapshots, screenshots, raw payloads | Platform | C |
| 45 | Observability | Logs, metrics, traces, LLM traces, cost | Platform | C |
| 46 | Dashboard | Config, pipeline builder, dossiers with evidence, approvals, inbox, analytics, resources | Experience | C (framework) + P (screens) |
| 47 | Notifications | Hot replies, failures, approvals waiting | Experience | C |
| 48 | Packaging and deployment | Same artefacts on a PC and on a VPS | Platform | C |
| 49 | Model gateway and routing | Task → model, fallback, budget, structured output | Harness | C (library) + P (policy) |
| 50 | Evaluation harness | Golden sets for extraction, qualification, writing and reply classification; run before swapping a model | Platform | C |

---

## C. Proposed architecture (pre-research)

**Shape.**
- A modular monolith: "MonolithFirst" (Fowler, 2015). That means one codebase and one database, run as several process types.
- Hexagonal ports and adapters (Cockburn, 2005). The Intelligence Engine depends only on capability *ports*; provider code lives in adapters.
- Each adapter is an anti-corruption layer (Evans, *Domain-Driven Design*, 2003): provider data is translated into Tensor's canonical model at the boundary and never leaks inward.

```
Dashboard (web)  ──►  Tensor API  ──►  Control plane: campaigns · ICPs · offers · pipelines · policies
                                          (versioned JSON documents, JSON-Schema-validated, edited visually)
                                   │
                                   ▼
                    Intelligence Engine (pipeline stages = durable workflows)
                    each stage declares: required facts + freshness → asks Knowledge first
                                   │                                   ▲
                                   ▼                                   │
                    Knowledge layer: canonical entities · facts (field-level, provenance, TTL)
                                     · evidence (snapshots, screenshots, raw responses)
                                     · request ledger (single-flight + response cache)
                                   │ missing / stale facts only
                                   ▼
                    Harness:  Capability API
                              → Resource Manager (registry · consumption models · quota ledger ·
                                                  router · health · circuit breakers · rate limits)
                              → Harness Operator (adapters: Apollo · Clay · Meta · registries · web ·
                                                  verifiers · LLMs · Claude CLI · …)
                                   │
Outreach: EmailProvider port → adapters · mailbox pool · scheduler · inbound (bounces, replies)
Platform: Postgres (system of record + workflow state) · object storage (local FS → S3-compatible)
          · workflow engine · workers (general / browser / LLM) · observability · Docker Compose
```

### Contracts that must never change (the foundation)

1. **CapabilityRequest**
   - Request: `{capability, input (typed), constraints {max_cost, freshness, min_quality, deadline}, idempotency_key, requested_by (campaign, job)}`.
   - Result: `CapabilityResult {status, data (canonical), facts[], raw_ref, usage[] {provider, connection, unit, amount}, provenance}`.
   - The Intelligence Engine sees only this pair.
2. **Fact**
   - Fields: `{entity_type, entity_id, attribute, value, source {provider, connection, request_id, url}, observed_at, verified_at, expires_at, confidence, evidence_ids[]}`.
   - Facts are appended, never overwritten in place, so history survives a provider change.
   - The provenance vocabulary follows W3C PROV-O (2013): entity, activity, agent.
3. **Observation vs Inference**
   - An *observation* is a fact about the prospect with evidence.
   - An *inference* is `{hypothesis, offer, based_on: observation_ids, reasoning, confidence}`.
   - Only observations may be stated as fact in an email.
4. **Consumption model per connection**
   - Shape: `units[] {name, limit, period/reset rule, charged_on (success | attempt), price_per_unit}`, plus rate and concurrency limits.
   - It is described, not assumed: "credits" is just one unit name.
5. **Pipeline definition**
   - An ordered graph of stages.
   - Each stage has: `type`, `config`, the facts it requires, its output, and its gate.
6. **EmailProvider port:** `send()`, `check_status()`, `handle_bounce()`, `track_reply()`, `suppress()`.

### Demand-driven fact acquisition (the "never pay twice" loop)

This is the core of the Knowledge layer **[mine]**. The idea resembles demand-driven rebuilding in build systems: Mokhov, Mitchell & Peyton Jones, "Build systems à la carte", 2018. For each required fact `(entity, attribute, freshness)`:

1. Look it up in the fact store, with three outcomes:
   - **fresh:** use it.
   - **stale:** refresh only if the decision depending on it is sensitive to staleness (a per-attribute policy). Otherwise use it and flag it; stale-while-revalidate semantics.
   - **missing:** continue to step 2.
2. Derive it if possible from evidence already held: rules first, then a cheap model.
3. Otherwise issue a CapabilityRequest:
   - The request is hashed; identical in-flight requests coalesce (single-flight).
   - Completed identical requests within their TTL return the stored response.
4. The Resource Manager routes and reserves units. The Harness Operator executes and normalises. Facts and the raw response are stored, then returned.

### Routing (Resource Manager)

**[mine]**, with circuit breaker and bulkhead patterns from Nygard, *Release It!*, 2007:

1. Filter connections. Keep those that:
   - offer the capability;
   - are healthy (circuit closed, not cooling down);
   - are allowed by policy (campaign, country, ToS-legitimacy flag);
   - have capacity left.
2. Rank them: free-with-capacity first, then cheapest paid within budget. Break ties on historical success and data quality *for this capability*, then on priority.
3. Atomically reserve units, execute, then settle the actual usage.
4. Handle errors by class:
   - **Rate limit:** cooldown.
   - **Auth failure:** disable the connection and alert.
   - **Transient error:** back off and retry.
   - **Repeated failures:** open the circuit.
   - **Empty result:** try the next connection only if the policy allows the extra cost.

### Process topology (same code, different entry points)

- `api` serves the dashboard and webhooks.
- `worker` runs general stages.
- `worker-browser` runs Playwright-class work.
- `scheduler` handles cron triggers.
- `dashboard` is the web UI.

Locally these run under Docker Compose on the founder's PC. On a VPS the same Compose file runs; scaling means more worker replicas. Configuration comes from the environment: the twelve-factor app (Wiggins, 2011).

### Questions the research must answer (one worker each)

| Q | Question | Research file |
|---|---|---|
| Q1 | What can Apollo, Clay, Meta Ad Library and public registries do per unit, through an API, on free or trial plans, and under what ToS (including multiple connections)? Does Clay replace parts of our enrichment logic? | research/r1-data-providers.md |
| Q2 | Which US/Canada B2B verticals buy website and AI-workflow work from agencies in 2026? What evidence and official datasets support vertical selection? | research/r2-market.md |
| Q3 | What is a legal, ToS-compliant, near-$0 sending path for low-volume cold email to the US and Canada? Deliverability, bounces, replies, suppression, CASL. | research/r3-email.md |
| Q4 | Which durable-workflow, queue, cache/single-flight, entity-resolution, observability and packaging components fit a local-first, VPS-later Python core? | research/r4-platform.md |
| Q5 | Should the dashboard and pipeline builder be built (framework + node editor + schema forms) or adopted (Windmill / Appsmith / n8n / …)? Which browser, render and website-audit tools give evidence for Offer A? | research/r5-ui-and-web.md |
| Q6 | Model layer: Bedrock credits and endpoints, OpenRouter, Claude CLI headless use and terms, gateways, structured output, evals, Hermes, MCP servers. | research/r6-ai-layer.md |

---

# Part 2 — after resource research (2026-10-03)

How the research was done:
- Six research workers produced `research/r1-data-providers.md`, `r2-market.md`, `r3-email.md`, `r4-platform.md`, `r5-ui-and-web.md` and `r6-ai-layer.md`. Earlier verified files (`research/00`, `01`, `02`, `04`) were reused rather than redone.
- **Method limit:** this container's network blocked direct fetches of most vendor and government pages, and the Firecrawl account ran out of credits. Many vendor and legal facts therefore come from search summaries of official pages, not verbatim reads.
- Each research file marks this. Facts marked "re-verify" must be re-checked before Phase 3 relies on them.

## D. Existing components per capability

"Build" means Tensor-owned code. "Thin" means an adapter or glue only. Capability numbers refer to the table in section B.

| Capability (B #) | Existing resource | Open source | Free option | Paid upgrade | Build ourselves? | Recommendation |
|---|---|---|---|---|---|---|
| Campaign, ICP, offer, policy config (1–4) | – | Pydantic → JSON Schema; RJSF | All free | – | Yes; the schemas are the product | Versioned JSON documents in Postgres, validated against Pydantic-generated JSON Schema and edited through RJSF forms (r5) |
| Pipeline definition and editor (3) | n8n, Kestra, Windmill: **rejected**, because each would own a second copy of the pipeline (r5) | React Flow (MIT) | Free | React Flow Pro $169/mo, optional | Yes: editor + interpreter | React Flow editor with a palette generated from stage schemas; Tensor's interpreter runs it on DBOS (r4, r5) |
| Secrets (5) | Infisical, Doppler | sops + age | Free | Infisical / Doppler paid | No | An encrypted sops file in git; connections reference secret *names* only (r4) |
| Canonical model, facts, evidence, freshness (6, 8–10) | Apollo and Clay hold data only in their own schemas | Postgres 18 | Free | Managed Postgres later | **Yes, the core asset** | Own schema (G1): append-only facts, a TTL per attribute, evidence blobs keyed by SHA-256 |
| Entity resolution (7) | – | tldextract (Public Suffix List, private suffixes on), pg_trgm, RapidFuzz; Splink later | Free | – | Rules yes, matcher no | Deterministic keys (registrable domain, email, LinkedIn URL) plus fuzzy tie-breaks; Splink when volume justifies it (r4) |
| Single-flight, dedup, response cache (11) | – | DBOS workflow and dedup IDs; Postgres unique index | Free | – | Thin `capability_request` ledger | request_hash = workflow ID; the ledger row is the TTL cache; no Redis (r4) |
| Workflows, queues, schedules, rate limits (17, 41, 42) | Supabase pgmq / pg_cron | **DBOS Transact (MIT)**; runner-up Hatchet | Free, no extra server | DBOS Conductor, optional | Thin WorkflowPort + interpreter | DBOS queues per worker class carry each provider's concurrency, rate limit, priority and dedup settings (r4) |
| Capability catalog, adapters, registry, metering, routing, health (12–17) | Clay and Apollo route only *inside* their own products | No mature open-source manager for metered SaaS data providers found. LiteLLM and Portkey do this for LLMs only **[mine]** | – | – | **Yes, proprietary core** | Build it; borrow gateway ideas for LLM routing (r6) |
| Company discovery (19) | Apollo org search (1 credit per page); Clay MCP `search-companies` | Overture Places, Foursquare OS Places (02) | Apollo Free 900 credits/yr; open place data | Apollo Basic $49/user/mo; Clay | Strategy only | Open data plus Apollo; Clay MCP for manual spot checks (r1) |
| Decision maker (27) | Apollo people search (0 credits per its docs); Clay `search-contacts` | – | Apollo Free (needs a work-email account) | Apollo Basic | No | Apollo API through **one** connection, per its ToS (r1) |
| Email find (28) | Hunter, Anymailfinder, Icypeas, Apollo enrichment | Pattern guess + verify | Hunter 50/mo; Anymailfinder 100 and Icypeas 50 one-time; Apollo 1 credit, charged only when found | Icypeas $19/mo, Anymailfinder $29/mo | Pattern generator only | Order: site-published address first (required for Canada, see F3), then pattern + verify, then paid finders for misses (r1, r3) |
| Email verify (28) | Reoon, ZeroBounce, MillionVerifier | AfterShip email-verifier (needs outbound port 25) | Reoon 20/day + 100 at signup; ZeroBounce 100/mo | MillionVerifier $89/50k; Reoon $11.90/10k | Thin adapter | Reoon first. Catch-all results are "risky", not sendable by default (r1) |
| Crawl, render, evidence capture (20) | Firecrawl: account out of credits; self-host is AGPL and lacks Fire-engine | **Playwright (Apache-2.0)**, Crawl4AI | Free, local | Browserbase from $20/mo | Thin `web.render` | Playwright in `worker-browser`. Store a PNG (fold and full page), DOM, headers, final URL, UTC time, viewport, tool version and SHA-256 (r5) |
| Website-audit evidence (21) | PageSpeed Insights v5, CrUX API | Lighthouse, axe-core | PSI 25,000/day (secondary source); CrUX 150/min | – | Rubric yes, measurement no | Cite only screenshot-confirmed facts and a *dated* CrUX p75 where one exists. Never cite Lighthouse scores, axe counts or revenue-loss figures (r5) |
| Workflow signals (22) | ATS JSON (Greenhouse, Lever, Ashby), Adzuna | wappalyzergo; JobSpy (PyPI release is from 2025-07) | Adzuna 250/day; ATS endpoints free | BuiltWith about $295/mo | Rubric yes | Crawl the site (forms, booking, chat, quote flows) plus job posts plus tech stack, stored as observations vs inferences (r1, 02) |
| Ad signals (23) | Meta, Google and LinkedIn ad libraries: **UI only** for US/CA commercial ads; scraping Meta's UI needs written permission | None legitimate | Manual | Scrapers carry ToS risk | No | **Human evidence task**: an operator checks and uploads a screenshot (r1) |
| Registries (23) | SEC EDGAR; ISED federal corporations; state bulk data (CO, CT, WA, OH); BC API | – | Free | OpenCorporates £2,250/yr | Thin adapters | Low priority: legal-name and existence checks (r1) |
| Market data (35) | Census BTOS, StatCan CSBC, BLS OEWS, SUSB/CBP, StatCan business counts | – | Free public data | – | Ingest + scoring | Versioned dataset tables; weighted sum over the criteria that have data, with coverage shown (r2) |
| Model gateway, structured output (49) | OpenRouter, LiteLLM, Portkey | LiteLLM (MIT), Portkey (MIT), instructor, PydanticAI, BAML | Ollama, Groq, OpenRouter `:free`, AWS credits | OpenRouter ~5.5% fee (secondary source) | Thin router | `openai` SDK per `base_url`, plus the `anthropic` SDK, plus a Claude CLI adapter; Pydantic schemas everywhere (r6) |
| Reasoning, offer choice, writing (24, 25, 30) | Claude | – | Claude CLI on the subscription, in own scripts | Sonnet 5.5 $2/$10, Opus 5.5 $4/$20 per MTok; Batch −50% | Prompts and rubrics | `claude -p --output-format json --json-schema …` now; an API-key worker later (r6) |
| Claim verification (31) | Anthropic Citations (cannot combine with structured outputs) | FActScore-style atomic checks | Exact-quote check in code | – | Yes | Code check first, then a verifier model from a *different family* (r6) |
| Evals (50) | promptfoo, DeepEval, Inspect | MIT / Apache-2.0 | Local | – | Golden sets | promptfoo plus pytest golden sets gate every model swap (r6) |
| Sending (36, 37) | Zoho on milvow.com: Free lacks SMTP/IMAP, and its policy demands "express and provable permission" | warmbly (young) | **No ToS-clean $0 path** | Google Workspace or Microsoft 365, about $7/user/mo, plus an outreach domain | Adapters + scheduler | One mailbox on a **separate outreach domain**, via Gmail API or Graph with OAuth (r3) |
| Bounces, complaints, suppression (38) | Yahoo CFL; Google Postmaster (hides low volume) | flufl.bounce | Free | – | Thin, plus our own counters | Parse DSNs; keep our own bounce and complaint rates; hashed suppression. `mailto:` List-Unsubscribe now; RFC 8058 one-click once a public URL exists (r3) |
| Replies (39) | Gmail watch, Graph webhooks (both need public HTTPS) | imap-tools, mail-parser-reply | IMAP IDLE | – | Thin threading | IMAP IDLE on the PC; push webhooks on a VPS (r3) |
| Legal basis (29) | – | – | – | – | **Yes** | US: CAN-SPAM fields. Canada: a CASL s.10(9)(b) evidence record, or no send (r3) |
| CRM state (40) | HubSpot Free, Twenty: **not researched** | – | – | – | Minimal | Outcome states live in Tensor; a CRM sync adapter later if needed **[mine]** |
| Dashboard and approvals (46, 32) | Appsmith (fallback); Retool (cloud-first) | shadcn/ui starter, TanStack Table, Recharts, RJSF | Free | Appsmith $15/user/mo | Yes, a thin SPA | Static SPA served by the Tensor API; local login replaces Clerk (r5) |
| Notifications (47) | Telegram Bot API | – | Free | – | Thin | Hot replies, approvals waiting, failures (04) |
| Observability (45) | Sentry free; Langfuse (too heavy to self-host) | structlog, OpenTelemetry, Phoenix | Free | Sentry Team | No | JSON logs + OTel → Phoenix locally; usage and cost in Postgres (r4) |
| Object storage (44) | Cloudflare R2 (10 GB free) | Garage (the MinIO repo is archived) | Local disk | R2 $0.015/GB-month | Thin BlobStore port | Local disk now; R2 or Garage later (r4) |
| DB, migrations, backups (43) | Supabase Free (pauses after a week, 500 MB) | Postgres 18, Alembic, pgBackRest | `postgres:18` in Docker | VPS or managed | No | Local Postgres 18; Alembic; nightly off-site `pg_dump` (r4) |
| Packaging, deploy (48) | Coolify, Kamal | Docker Compose | Docker Desktop (free for small businesses) | VPS | No | One Compose file on the PC and on the VPS (r4) |
| Agent chores (downloads, data pulls) | Claude Code CLI, Hermes Agent (MIT, 0.19.0) | Hermes | Subscription; Bedrock credits | API keys | Thin adapter | Hermes for mechanical jobs only, with outputs validated; never for schema-bound extraction (r6) |

## E. Resource registry

**The full registry is the "E rows" tables in `research/r1`–`r6`.** That is roughly 130 resources, each with the requested fields: URL, capability, pricing, free tier with unit and reset, trial, API, automation terms, limitations, licence, local/VPS fit, integration, maturity and relevance. Rejected and risky resources stay listed there with the reason.

The set the foundation actually uses:

| Resource | Capability | Free capacity (unit) | Paid path | Terms / licence notes | Integration | Source |
|---|---|---|---|---|---|---|
| Apollo.io | person.search, company.search, enrich | 900 credits/yr. People search 0 credits; org search 1 credit per page; enrichment 1 credit (+8 mobile), charged only when found | Basic $49/user/mo | **One self-serve account; no circumventing limits** | REST adapter; official MCP (mcp.apollo.io) for exploration | r1 |
| Clay | enrichment waterfalls, Claygent research | 100 data credits + 500 actions/mo; 500 credits on first MCP connect; no-result enrichments are free | Growth $495/mo for webhooks/HTTP API; "Clay API" is Enterprise | At $0, only through MCP or by hand | Agent or human executor now; API adapter at Growth | r1 |
| Overture / Foursquare OS Places | company.search (local businesses) | Free, open licences | – | Coverage varies | Bulk download | 02 |
| ATS JSON (Greenhouse, Lever, Ashby), Adzuna | jobs.lookup | Free; Adzuna 250/day | – | Adzuna key | REST | 02, r1 |
| Hunter / Anymailfinder / Icypeas | email.find | 50/mo; 100 one-time; 50 one-time | $19–49/mo | – | REST | 02, r1 |
| Reoon, MillionVerifier | email.verify | 20/day + 100 at signup; – | $11.90/10k; $89/50k | Catch-all is not charged (vendor claim, Low) | REST | r1 |
| Playwright (Python), Crawl4AI | web.render, web.fetch | Free | – | Apache-2.0 | Local `worker-browser` | r5, 02 |
| PageSpeed Insights v5, CrUX API | web.audit (performance, field data) | 25,000/day (secondary source); 150/min | – | Google API key; CrUX returns 404 for low-traffic sites | REST | r5 |
| wappalyzergo | tech.detect | Free | – | MIT (Go) | Local binary or sidecar | 02 |
| SEC EDGAR, ISED federal corporations | registry.lookup | Free / open data | – | – | REST or bulk | r1 |
| Claude: CLI now, API later | llm.reason, llm.write, agent research | Subscription, used through the unmodified CLI in own scripts | Sonnet 5.5 $2/$10; Haiku 4.5 $1/$5; Batch −50% | Subscription terms: "ordinary individual usage"; never inside LiteLLM or SDK code (01) | Subprocess adapter → API worker | 01, r6 |
| Ollama (local), Groq Free | llm.extract, llm.classify | Unlimited local; 1K requests/day and 200K tokens/day per model | – | Groq keeps data 30 days | OpenAI-compatible | 01 |
| AWS Bedrock (mantle endpoint) | open-weight models (gpt-oss, Qwen, GLM) | New-account credits: $100, plus up to $100 for activities, valid 6 months. Whether they cover Claude: UNVERIFIED | Pay per token; batch −50% | Bedrock API key or SigV4 | OpenAI-compatible `/v1` | r6 |
| DBOS Transact, PostgreSQL 18 | workflows, queues, system of record | Free | DBOS Conductor, optional | MIT / PostgreSQL licence | Python library + Docker image | r4 |
| React Flow, RJSF, shadcn/ui starter, TanStack Table, Recharts | dashboard | Free | React Flow Pro, optional | MIT / Apache-2.0 | npm | r5 |
| Google Workspace or Microsoft 365 + outreach domain | sending mailbox | **none** | About $7/user/mo + about $10–15/yr per domain | Exchange basic SMTP AUTH is off by default from end-Dec 2026, so use OAuth | Gmail API or Graph adapter | r3 |
| imap-tools, flufl.bounce, mail-parser-reply | replies, bounces | Free | – | Apache-2.0 / MIT | Library | r3 |
| promptfoo; structlog + OTel + Phoenix; Sentry; sops + age; Telegram | evals, observability, secrets, alerts | Free | Sentry Team | – | Library or container | r4, r6, 04 |
| Hermes Agent | agent chores on the PC | Free (MIT); spends Bedrock or OpenRouter credits | – | Validate every output | CLI `hermes -z … --usage-file` | r6 |

## F. Architecture revision (what the research changed)

1. **Executor types.** The harness now has four kinds of executor behind the *same* CapabilityRequest contract:
   - `api`: deterministic adapters, for Apollo, Reoon, PSI.
   - `local`: Playwright, wappalyzergo, Ollama.
   - `agent`: Claude CLI or Hermes with MCP tools. Outputs are schema-validated.
   - `human`: a dashboard task with an evidence upload.

   Why: Clay at $0 has no API, so it can only run as agent or human. US/CA ad intelligence has no legitimate API, so it is human. The router treats all four uniformly. Upgrading Clay to Growth simply adds an `api` connection; nothing above the harness changes. **[mine]**
2. **Connection legitimacy is data.**
   - Every connection records its `terms_basis`, and the policy caps connections per provider.
   - Apollo's ToS bars multiple self-serve accounts and circumventing limits, so its pool is one connection until paid seats exist.
   - The pool abstraction stays general, for licensed seats, workspaces and plans. The router refuses any connection without a terms basis.
3. **Legal basis is a hard gate before personalisation.**
   - Canada (CASL s.10(9)(b), proof on the sender per s.13): sendable only with a stored evidence record. The record holds the URL where the address was conspicuously published, a snapshot, the date, the absence of a "no unsolicited messages" notice, and role relevance.
   - So for Canada, contact acquisition starts from *published* addresses (crawl). Provider-guessed addresses are US-only by default.
   - US: CAN-SPAM fields (postal address, opt-out, honest headers).
   - Re-verify the CASL text and the penalty figure in the primary source before launch (r3).
4. **Sending.**
   - There is no ToS-clean $0 path. Zoho Free lacks SMTP/IMAP, and Zoho's policy requires express permission. SES and the transactional ESPs bar unconsented lists.
   - Cold mail must **not** go from milvow.com's Zoho mailbox: one policy strike would also hit the agency's real email.
   - The EmailProvider port gets four adapters: `gmail_api`, `graph`, `smtp_imap` and `manual`. `manual` means Tensor prepares the email, a human sends it, and the reply is logged.
   - Tensor keeps its own bounce and complaint rates, because Postmaster Tools hides low volume.
5. **Dashboard: build a thin SPA, do not adopt.** Pipeline JSON in Postgres stays the single source of truth. The React Flow palette and RJSF forms are generated from each stage's JSON Schema. Appsmith is the fallback.
6. **DBOS sits behind a WorkflowPort.**
   - request_hash = workflow ID gives single-flight. Queues per worker class carry each provider's limits.
   - Lock-in risk: DBOS is young (repo created 2024-07), and multi-worker crash recovery needs Conductor or self-managed executor IDs. The port keeps Hatchet as a swap target (r4).
7. **Model layer.**
   - The Claude CLI is an `agent` executor now, called with `--json-schema`. The docs say `--bare` (API key only) will become the `-p` default, so the API-key worker is planned rather than optional.
   - Small models (local, Groq, Bedrock open-weight) do extraction.
   - A different-family model verifies claims. Self-preference bias: Zheng et al. 2023 and Panickssery et al. 2024, both confirmed to exist in r6.
   - Anthropic Citations is not used, because it cannot combine with structured outputs; Tensor keeps its own claim → evidence map.
8. **The website-evidence policy is code.** r5's lists of facts safe and not safe to cite become rules in the claim verifier.
9. **The market module is a hypothesis store.** It scores only criteria that have data and always shows coverage (Keeney & Raiffa, 1976 weighted sum). Official datasets are ingested as versioned tables. Equal-size pilots give the only real measure of purchasing behaviour and saturation, because no public data measures them per vertical (r2).
10. **Local Postgres, not Supabase, is the system of record.** Supabase Free pauses and caps at 500 MB. Supabase stays optional for a hosted replica later.
11. **Firecrawl is out of the core path.** Its credits ran out; Playwright and Crawl4AI run locally for free.
12. **Uptime honesty.** On a PC Tensor is not 24/7. DBOS resumes queued and interrupted workflows on start; sending happens only in windows while the PC is on; replies are read from IMAP on start. True 24/7 starts at the VPS stage. No code changes.

## G. Final foundation (build this now; expand later)

### G1. Canonical data model

Every row has a stable UUIDv7 ID (built into PostgreSQL 18 as `uuidv7()`). No table mirrors a provider's schema. Provider IDs live in identifier tables.

| Entity | Purpose | Key fields |
|---|---|---|
| `market_hypothesis` | A vertical to test | naics, countries, size_band, offer_id, criteria{value, source, grade, retrieved_at}, coverage, status |
| `campaign` | One experiment | hypothesis_id, icp_id, offer_id, pipeline_version_id, policies{limits, approvals, providers, models, compliance}, status |
| `icp`, `offer` | Versioned definitions | rules / description, allowed_proof_points[] (only real proof may be claimed) |
| `pipeline`, `pipeline_version` | The pipeline graph | graph JSON (stages, config, gates), schema_version |
| `company` + `company_identifier` | Canonical company + provider and external IDs | registrable domain (unique), names, country, region, tz, naics, size_band |
| `contact` + `contact_identifier` | Canonical person | company_id, name, title, persona, emails[] with status |
| `fact` | Field-level knowledge (the cache) | entity_type, entity_id, attribute, value, source{connection_id, request_id, url}, observed_at, verified_at, expires_at, confidence, evidence_ids[], superseded_by |
| `current_fact` (view) | Best value per attribute | Preference order: verified > fresh > source priority > confidence. All values are kept. **[mine]** |
| `evidence` | Proof | kind (screenshot, dom, raw_response, quote, manual_upload), blob sha256, url, captured_at, tool and version, viewport |
| `signal` | Derived business signal | company_id, type, strength, fact_ids[], observed_at, expires_at |
| `observation` | A *seen* statement about the prospect | company_id, category (website, workflow, business), statement, evidence_ids[], citable, verified_by |
| `opportunity` | An *inferred* hypothesis | company_id, offer_id, hypothesis, observation_ids[], reasoning, confidence |
| `research_job`, `research_result` | Module runs | module, inputs, status, outputs (observation and opportunity ids), cost |
| `qualification` | Explainable verdict | campaign_id, company_id, gates{}, subscores{value, fact_ids}, score, coverage, verdict, rules_version |
| `legal_basis` | Per contact × country | regime (CAN-SPAM, CASL), basis, evidence_ids[], checked_at, valid_until |
| `provider`, `provider_connection` | Resource pool | executor_type (api, local, agent, human), auth_ref (secret name), plan, **terms_basis**, priority, status, policy tags |
| `provider_capability` | What a connection can do | connection × capability × adapter_version × expected unit cost × observed success and quality |
| `consumption_model` | How the provider meters | units[]{name, limit, period, reset_anchor, charged_on: success or attempt}, rate and concurrency limits |
| `provider_usage`, `quota_window` | Metering | request_id, unit, amount, estimated vs actual; window totals |
| `connection_health` | Breakers | circuit state, cooldown_until, consecutive_failures, last_success, last_failure |
| `provider_response` | Raw payloads | blob ref, hash, status, latency |
| `capability_request` | Ledger: single-flight + cache | **request_hash (unique)**, capability, input, constraints, status, result_ref, expires_at, workflow_id |
| `mailbox`, `sending_domain` | Sending pool | provider adapter, caps, ramp, auth status (SPF, DKIM, DMARC) |
| `email`, `email_event`, `reply` | Outreach | email: draft, claims[]→evidence_ids, verifier results, approval. events: queued, sent, delivered, bounced, complained, unsubscribed, replied. reply: classification, confidence, handled |
| `suppression` | Never contact | sha256(email or domain), reason |
| `approval`, `outcome`, `learning`, `event` | Human decisions, results, lessons, audit trail | diffs and reasons; meeting, proposal, won/lost, value; proposed or accepted; append-only log |

The schema sketch in `research/sketches/data-model-v0.sql` was tested on Postgres 16. Phase 3 rewrites it to this model.

### G2. Freshness defaults (per attribute, editable per campaign) [mine]

| Attribute class | TTL | Must be refreshed before |
|---|---|---|
| Industry, legal name | 365 days | – |
| Size, revenue band | 180 days | Qualification, if older than the TTL |
| Tech stack, decision maker | 90 days | Personalisation |
| Website observations, PSI/CrUX | 30 days | **Any email that cites them** |
| Job postings | 14 days | Any email that cites them |
| Ads (manual) | 7 days | Any email that cites them |
| Email verification | 60 days | **Send, if older than 30 days** |
| CASL publication evidence | 90 days | **Send to Canada** |

### G3. One capability call, end to end

1. A stage declares the facts it needs.
2. It looks in `current_fact`: fresh → use it; stale → apply the freshness policy; missing → try to derive it.
3. Otherwise it builds a CapabilityRequest, and request_hash becomes the DBOS workflow ID:
   - an identical in-flight call joins the existing one;
   - an identical finished call within its TTL returns the stored result.
4. The Resource Manager filters connections by capability, health, policy, terms basis and remaining capacity. It ranks them free-first, then by cost, then by historical quality, and reserves units.
5. The executor runs: api, local, agent or human.
6. The adapter normalises the result into canonical facts plus evidence (anti-corruption layer), then settles usage and records health.
7. Facts are written, and the stage continues.

### G4. Prospect-quality model

Two parts, gates then scores:
- **Hard gates (all must pass):** ICP rules, not suppressed, one live conversation per company, legal basis valid, a verified email that is not catch-all, and at least two *citable* observations.
- **Weighted sub-scores (Keeney & Raiffa, 1976 weighted sum; weights set in the campaign):** ICP fit, offer fit (opportunity strength), evidence strength, decision-maker confidence, contactability, business signal, freshness, personalisation depth.

Every sub-score points to its facts. Coverage is shown beside the score, and a high score with low coverage is flagged. **[mine]**

### G5. Personalisation and verification chain

1. The dossier is reduced to the 1–2 strongest citable observations, one opportunity and the offer.
2. Claude returns JSON: `{subject, body, claims[{text, evidence_id, kind: observed|inferred}]}`.
3. Code checks:
   - every observed claim has evidence, and its quoted text matches;
   - inferred claims are phrased as possibilities;
   - length 60–110 words, one CTA, no links in the first touch;
   - banned phrases;
   - no proof points beyond `offer.allowed_proof_points`.
4. A different-family model verifies each claim against its evidence (FActScore-style; Min et al. 2023).
5. A human approves, seeing evidence side by side.
6. Send.

### G6. Processes (one Compose file, PC and VPS)

`postgres` · `api` (serves the SPA, webhooks and the Tensor API; FastAPI assumed, not researched) · `worker` (general stages) · `worker-browser` (Playwright) · `worker-llm` (Claude CLI with concurrency 1–2; Ollama optional) · DBOS schedules inside the workers · `phoenix` (optional).

Scaling means more replicas per worker class and higher queue limits.

### G7. Build vs reuse (final)

**Build (proprietary):**
- the canonical model, fact and evidence store, and freshness policy;
- the capability catalog, Resource Manager and adapters;
- the research rubrics (website and workflow), opportunity inference, offer selection and qualification;
- the legal-basis gate, personalisation and verification;
- the pipeline interpreter;
- the dashboard screens.

**Reuse:** Postgres, DBOS, Playwright, Crawl4AI, wappalyzergo, PSI/CrUX, Apollo, Clay (MCP), Reoon, Hunter, Claude, Ollama/Groq/Bedrock, imap-tools, flufl.bounce, React Flow, RJSF, shadcn/ui, TanStack Table, Recharts, structlog/OTel/Phoenix, sops, Compose.

### G8. Phase 3 order (each milestone is usable on its own) [mine]

1. **M1:** schema + capability ledger + Resource Manager + DBOS. Adapters: Apollo, Playwright, Reoon, Claude CLI, Ollama/Groq. Dashboard: campaign config + dossier view.
2. **M2:** website-audit and workflow-signal modules, opportunity inference, offer selection, qualification, personalisation + verifier, approval queue.
3. **M3:** outreach mailbox adapter, sending scheduler, bounces and replies, suppression, funnel analytics, provider-usage analytics.
4. **M4:** market-hypothesis module with dataset ingestion, the learning loop, and the pipeline editor (React Flow).

## H. Upgrade path

| Stage | Trigger | Monthly spend | What changes (config, adapters, capacity) | What stays |
|---|---|---|---|---|
| 0: $0-ish (now) | – | **About $8 if you approve one outreach mailbox, otherwise $0** with manual sending only | PC + Docker. Claude CLI on the subscription. Ollama/Groq/AWS credits for small tasks. Apollo Free, Reoon free, Hunter free. Playwright, PSI. Manual ad checks. 10–20 emails/day from one mailbox. | All contracts, schema and pipeline |
| 1: first revenue | First client | About $50–150 | An Anthropic API key (Batch, caching) replaces the CLI for production. A cheap VPS runs the same Compose file 24/7. 2–3 mailboxes on 2 domains. MillionVerifier. Apollo Basic seat. Nightly backups to R2. Gmail push or Graph webhooks (public HTTPS). One-click unsubscribe. | Same |
| 2: moderate budget | Repeatable reply rate | About $300–800 | Clay Growth **only if** measured cost per qualified dossier beats our own enrichment. More worker replicas and higher queue limits. Phoenix/OTel dashboards. Paid ad-intelligence or data sources as new connections. | Same |
| 3: serious scale | Volume or team growth | Variable | Managed Postgres, more VPS nodes, browser-worker pool, multiple sending domains per offer. Hatchet or Temporal behind the WorkflowPort only if DBOS limits bite. Multi-user auth. | Same contracts and data. History is retained because facts are provider-neutral and append-only. |

## The final test

| Question | Answer | How / weakness |
|---|---|---|
| Runs on one computer? | **Yes** | Docker Compose on Windows (Docker Desktop is free for small businesses) |
| Almost no infrastructure budget? | **Yes, with one exception** | Compliant cold sending needs about $8/mo (mailbox + domain). At strict $0, sending is manual and at policy risk. **Weakness: W1** |
| Uses free capacity efficiently? | **Yes** | Quota ledger + router, free-first; field-level cache; single-flight |
| Researches a limited number of high-quality prospects? | **Yes** | Filter cascade; research only on survivors |
| Produces genuinely personalised emails? | **Yes** | Evidence-only context, claim verifier, human approval |
| Visual campaign configuration? | **Yes** | RJSF forms from schemas. **Weakness W2:** the dashboard must be built (thin SPA) |
| See the entire pipeline? | **Yes** | Funnel view from Discovered through Clients; React Flow graph arrives in M4 |
| Inspect why a prospect qualified? | **Yes** | `qualification` sub-scores → facts → evidence |
| Inspect evidence behind personalisation? | **Yes** | `email.claims[]` → evidence_id → screenshot or quote |
| See what was sent and the replies? | **Yes** | email, email_event, reply. Needs a mailbox with IMAP or API: Zoho Free has neither (**W1**) |
| Move to a VPS without rewriting? | **Yes** | Same Compose file. **Weakness W3:** DBOS multi-worker crash recovery needs executor IDs or Conductor |
| Add paid Apollo or Clay capacity? | **Yes** | New `provider_connection` rows. Clay automation needs the Growth plan (**W4**) |
| Add other data providers? | **Yes** | One adapter + capability bindings + consumption model |
| Add better AI models? | **Yes** | Router config; golden-set gate before the swap |
| Add more email providers or mailboxes? | **Yes** | EmailProvider adapters + mailbox pool |
| Add workers, concurrency, volume? | **Yes** | Replicas per worker class; queue limits; sending stays capped by reputation |
| Add countries? | **Yes** | Country = a compliance regime + a legal-basis rule. **Weakness W5:** each new regime needs legal rules written first |
| Add industries, offers, research modules? | **Yes** | Hypothesis, offer and research-module registries; new module = new stage type |
| Replace one provider without rebuilding? | **Yes** | Ports, adapters and the anti-corruption layer |
| Keep the historical knowledge base? | **Yes** | Append-only, provider-neutral facts with provenance |

Weaknesses to manage:
- **W1 — sending:** cold sending cannot be both free and compliant.
- **W2 — dashboard:** it is build effort.
- **W3 — DBOS:** it is young (mitigated by the WorkflowPort).
- **W4 — Clay:** automation needs the $495/mo plan.
- **W5 — new countries:** legal work per country.
- **W6 — uptime:** a PC is not 24/7 until the VPS.
- **W7 — subscription CLI path:** it may change, with `--bare` becoming the default, so the API-key worker is pre-planned.
- **W8 — market data:** vertical selection data is incomplete. BLS, Census and StatCan tables were blocked in this container and need downloading on your PC or through Hermes.

## Decisions needed before Phase 3

1. **Email:** approve about $8/mo (one Google Workspace or Microsoft 365 mailbox) plus about $10–15/yr (a separate outreach domain). The alternative is strict $0 with manual sending, accepting the policy risk.
2. **Pilot hypotheses:**
   - The research suggests equal-size pilots across 3–4 verticals from r2's candidates. These are judgements, not facts:
     - accounting/bookkeeping 5412 (Offer B);
     - insurance agencies 5242 (B);
     - consulting 5416 or architecture/engineering 5413 (A);
     - one low-maturity vertical such as machinery wholesale 4238 or fabricated metal 332 (both offers).
   - The default ICP is 20–249 staff.
   - Pick, or change, these.
3. **Accounts:** does an Apollo account exist (it needs a work email)? Which Clay plan?
4. **PC:** confirm Windows + Docker Desktop is acceptable.
5. **Hermes:** may it download the blocked government datasets (BTOS, OEWS, SUSB/CBP, StatCan) on your PC, and with what spend limit?
6. **Still open from earlier:** are milvow.com's Cigna/Aetna case studies real? Offer A emails send prospects to your site. Also, install the Claude GitHub App so commits can be pushed.

---

### Provenance (load-bearing claims)

Grades follow GRADE: High, Moderate, Low.

- **Clay plan gating, Apollo credits and ToS, ad-library coverage, verifier tiers:** r1, from search summaries of official pages. **Moderate**; re-verify.
- **BTOS and StatCan AI-use figures:** r2. **High** (official surveys; some read via summaries).
- **CASL s.10(9)(b), s.13 and penalties; CAN-SPAM; Google, Yahoo and Microsoft sender rules; Zoho and ESP policies:** r3. **High** for the statutes and **Moderate** for policies (summaries); re-verify verbatim.
- **Cold-email benchmarks:** r3. **Low** (every producer sells in the category; producers differ about 7×).
- **DBOS features, MinIO archived, Langfuse requirements:** r4. **Moderate to High** (official repos and docs).
- **React Flow, RJSF and PSI/CrUX facts:** r5. **Moderate** (the PSI quota is secondary).
- **Claude CLI flags and Anthropic API features:** r6. **High** (official docs).
- **AWS credits:** r6. **Moderate**; Claude coverage UNVERIFIED.
- **Papers:** Gao 2023 (arXiv 2305.14627), Min 2023 (2305.14251), Zheng 2023 (2306.05685), Panickssery 2024 (2404.13076). All confirmed to exist (r6).

### Mine, not published

- The executor-type split (api, local, agent, human).
- The legitimacy-as-data rule.
- The demand-driven fact loop and the `current_fact` preference order.
- The TTL defaults.
- The gate list and sub-score set.
- The email length and links rules.
- The build order.
- The upgrade-stage spend bands.
- The pilot vertical shortlist, which is r2's [J] judgement.

### What I could not verify

- **Market:** BLS OEWS, SUSB/CBP and StatCan counts (blocked), so 9 of 10 market criteria are unscored.
- **Prices from secondary sources:** Clay Enterprise API details, Sentry, Doppler, Infisical and DBOS Conductor prices, and the PSI daily quota.
- **Model access:** whether AWS credits pay for Claude on Bedrock, and whether the Clay MCP works in headless CLI mode with its auth.
- **Your environment:** whether outbound port 25 is open on your PC, which SMTP verification would need.
- **Not researched:** the CRM choice (HubSpot Free or Twenty).
