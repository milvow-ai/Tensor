# Tensor — foundation: problem, capabilities, architecture, resources

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
