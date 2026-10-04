# HANDOFF — Harness Farm + Farm Console

> **Scope update — owner, 2026-10-05 (overrides narrower wording below).** Harness Farm is a **general, independent product**: one MCP connection through which any AI (Claude Code, Codex, …) reaches **every MCP server and every AI CLI** the owner connects — not a GTM/email tool. (1) **Any MCP server is added by config, no code**, imported in bulk from Claude/Codex configs, and its tools reach the caller **exactly as the original server returns them**, with multi-account pools, quotas, budgets and run history on top (brief `OPEN1`). (2) **AI orchestration**: the main AI starts tasks on other AI accounts (several Claude accounts on different emails, Codex, Gemini, Hermes — CLI, as many as the owner logs in), checks them, reads exact results, and iterates with the same worker/session; failures say which worker and why (brief `AIP2`). Typed capabilities (`verify_email`, …) and the Tensor/GTM providers are one use case and stay an optional layer. The Console's Integrations and AI Pools screens are the main control surfaces.

Written 2026-10-03 by the previous Claude session, for the next session working on this repo.

**Read this whole file once. Then start with Phase 0. Do not start building before the user says "go".**

---

## 0. How to use this file

| Order | Do | Do not |
|---|---|---|
| 1 | Read §1–§6 (context and design: about 10 minutes of reading) | Re-research anything already in `research/`; it is verified and dated |
| 2 | Run **Phase 0 — Pre-setup** (§8) and ask the user the §8.1 questions | Spend money or credits, create accounts, or build features |
| 3 | Show the user the plan (≤ 10 lines) and wait for "go" | Skip the plan gate |
| 4 | **Phase 1 — Build Harness Farm** (§9), milestone by milestone, each verified | Re-design: the design decisions in §3–§5 are made |
| 5 | **Phase 2 — Build Farm Console** (§10) and deploy it to Vercel | Build Tensor's outreach dashboard or pipeline (later, separate effort) |

Method: **plan → design (already done here) → act → complete**. Every milestone has a time box. If a milestone runs 50% over its box, stop and tell the user why. Do not push on silently.

---

## 1. Mission

Build **Harness Farm**: one MCP connection through which Claude and Hermes reach an entire managed ecosystem of tools.
- **The resource ecosystem:** APIs, MCP servers, browsers, models, agents, local workers, and *multiple accounts per provider*.
- **What the Farm adds on top:** capability routing, fallback, multi-account pools, quota and bill control, a fact cache, evidence and run history.

Then build **Farm Console**, the dashboard that configures and controls the Farm, deployed on Vercel.

```
Claude ─┐                      ┌─ Apollo (accts) ─ Clay (7 accts) ─ Reoon ─ ZeroBounce ─ Hunter
Hermes ─┼── ONE MCP ──► FARM ──┼─ Groq / Ollama / Bedrock (via Bifrost) ─ Browser (Crawl4AI)
        │   connection         ├─ PageSpeed ─ Adzuna ─ job boards ─ other MCP servers
Console ┘ (admin API)          └─ Hermes (agent resource) ─ local scripts ─ human tasks
```

The agent asks for a **capability** ("verify this email", "enrich this company", "research this website"). The Farm decides how:
- cache first;
- which resource and which account;
- reserve capacity, execute, fall back on failure;
- record the cost, evidence and trajectory;
- return a structured result.

**The Farm makes no AI decisions itself.** Recipes are fixed code paths. Open-ended work is delegated to an agent resource (Hermes).

**Order of the overall project:** Harness Farm → real work through it → observe → Farm Console → later the Tensor dashboard and pipeline. **Do not build Tensor's dashboard now.**

---

## 2. The user: preferences you must follow

- **Very token-sensitive.** Lean execution, small steps, no research sprees. Earlier sessions were criticised for token waste.
- **Wants to be asked when something is unclear,** rather than having Claude assume.
- **Engineering focus.** The user owns the accounts they connect (paid, e.g. 7 Clay accounts). Do not lecture about provider rules; build the multi-account pool as specified.
- **Runtime:**
  - The Farm runs on **the user's own PC (likely Windows)**: no VPS now; a VPS later must need no rewrite.
  - Budget is near $0 until revenue.
- **Tools the user has:**
  - Claude (plan: ask);
  - Hermes Agent on the PC, using AWS Bedrock models (`--provider custom:bedrock-mantle`, `qwen.qwen3-coder-next`, `zai.glm-5`);
  - wants **Gemini 3.8 via the Antigravity CLI** for bulk coding;
  - an orchestrate skill (installed in `.claude/`).

---

## 3. Current state of the repo (2026-10-03)

| Item | State |
|---|---|
| Design docs | `docs/TENSOR-FOUNDATION.md` (A–H) and `docs/HERMES-INTEGRATION.md`: done |
| Research | `research/00`–`04`, `r1`–`r6`, `h2`: done; grep them when you need a fact |
| Schema sketch | `research/sketches/data-model-v0.sql`, tested on Postgres 16, with an atomic `consume_quota()`. Reuse its ideas |
| Library manifest | `research/library.tsv` (17 repos pinned) + `scripts/fetch-library.sh` |
| Hermes repo | Pinned at `c8301ea6` in the manifest |
| Memory export | `memory/mem0-import.jsonl` (41 items). Import it into mem0 if that connector is connected |
| Code | **None yet.** Phase 1 creates it |
| **Blocker** | GitHub push to `milvow-ai/Tensor` returned 403. The user must install the Claude GitHub App. Check this first in Phase 0 |
| Cloud-container limit | Network allowlist (GitHub, PyPI, npm only). **The Farm must be built and run on the user's PC**, where the network is open and Hermes and the CLIs live |
| Firecrawl | Account out of credits; not needed (Crawl4AI and Playwright are local) |

---

## 4. Harness Farm design (decided; implement this)

### 4.1 Layers

```
MCP gateway (FastMCP 4)         one endpoint (stdio locally; Streamable HTTP later)
  middleware chain:             auth → policy → cache check → single-flight → trajectory
Capability tools                high-level (verify_email, enrich_company, find_person,
                                analyze_website, research_company…) + infra (get_capacity, get_run…)
Router                          capability → eligible connections → rank → reserve → execute
                                → commit/release → fallback
Resource Manager                registry · consumption models · quota ledger · health/circuit ·
                                cooldowns · multi-account pool · Account Manager · budgets
Executors                       api · mcp (FastMCP proxy) · llm (via Bifrost) · agent (Hermes)
                                · browser (Crawl4AI/Playwright) · local · human
State                           Postgres (Supabase project) · evidence files on local disk
                                (+ thumbnails in Supabase Storage)
Durability                      DBOS: recipes as durable workflows; one queue per connection
                                (concurrency + rate limit); request hash = workflow ID (dedup)
```

### 4.2 Capability → resource graph (router order; extend through config)

| Capability | Resources, in default order |
|---|---|
| `verify_email` | Reoon → ZeroBounce → one-time trials (Emailable, Bouncer) → syntax/MX check only (marks "unverified") |
| `find_email` | Published on the site (crawl) → pattern + verify → Hunter → Anymailfinder / Icypeas → Apollo enrichment |
| `find_person` | Apollo people search (0 credits per its docs) → Clay pool → site team page |
| `enrich_company` | Cache → own crawl + cheap model extraction → Apollo → Clay pool |
| `analyze_website` | Crawl4AI capture (evidence) + PageSpeed/CrUX + wappalyzergo |
| `jobs_lookup` | Greenhouse / Lever / Ashby JSON → Adzuna |
| `extract` / `classify` | Groq → Ollama (local) → Bedrock, all via Bifrost |
| `research_company` (recipe) | analyze_website → find_person → find_email → verify_email → optional Hermes exploration step |
| `agent_task` | Hermes (role profile; Farm MCP as its toolset) |

### 4.3 Ranking and multi-account strategies

**Eligibility:** connection enabled · healthy · capacity ≥ estimate · scope covers the task.

**Rank:**
- **Credit price:** free first, then pay-only-if-found, then cheapest per unit.
- **Spending order:** soonest-reset credits first.
- **Tie-breaks:** best success history, then priority.

**Per-request strategy** (default per capability, overridable by the caller):

| Strategy | Behaviour |
|---|---|
| `failover` (default) | One account until low, then the next |
| `most_remaining` | Balance by remaining credits |
| `round_robin` | Rotate per request |
| `parallel_split` | Split a batch across all eligible accounts, each within its own concurrency |
| `sticky` | Keep one job on one account (needed for Clay: tables live per workspace) |
| `fit_check` | Pick the account that can finish the whole job |
| `pin` | The caller forces a connection id |

**Mid-batch failure:** re-queue the remaining items to another account. Each item has an idempotency key, so there are no double charges.

### 4.4 Account Manager (active; scheduled jobs)

| Area | What it does |
|---|---|
| Inventory | Provider, plan, price, billing day, allowances, reset anchor, owner, status, scope. **No payment card data.** |
| Balance sync | Hourly/daily from provider balance or usage APIs where they exist. Otherwise a Hermes read of the provider dashboard (saved login), or manual entry. The ledger keeps `estimated` vs `actual` |
| Auto-adjust | Pause near the limit or on errors; resume at the reset anchor; daily pacing budget (remaining ÷ days to reset); "needs login" state |
| Bill control | Monthly budget per provider + a global cap, spend so far this month, month-end forecast, alerts at 50/80/100%. **Hard stop at the cap** in the router |
| Renewals | Calendar; Telegram reminder 3 days before, showing usage % → keep or cancel. Upgrades and cancellations become **human tasks with a link**; payments are never automated |
| Value | Cost per result per account and plan; idle paid accounts flagged |
| Audit | Every change: who, what, when |

### 4.5 Data model (Postgres; add `workspace_id` to every table now, single workspace for the moment)

- **Accounts, quota and health:** `providers`, `connections` (auth_ref = env var or token-store id, executor, scope, priority, strategy, status), `consumption_units` (unit, limit, period, reset_anchor, charged_on), `quota_reservations`, `usage_events` (estimated or actual), `balance_snapshots`, `connection_health` (circuit, cooldown_until, failures).
- **Billing:** `plans`, `billing_events`, `budgets`, `alerts`.
- **Capabilities and requests:** `capabilities`, `capability_routes` (capability → connection order), `capability_requests` (request_hash UNIQUE, status, result_ref, expires_at).
- **Knowledge:** `entities` (companies / people, canonical keys), `facts` (entity, attribute, value, source connection, observed_at, expires_at, confidence, evidence_ids), `evidence` (sha256, path, url, captured_at, tool version).
- **Runs and control:** `runs` and `run_events` (trajectories: plan, resource choices, fallbacks, cost, status), `farm_commands` (the Console's command queue, see §5.3), `audit_events`.

### 4.6 Interfaces

**MCP tools for agents.** Capability tools plus `get_capacity`, `list_resources`, `get_run`, `get_trajectory`, `get_evidence`, `get_usage`. Agents get **no** admin tools.

**Admin (human only):**
- the Console via the Supabase command queue;
- a CLI: `farm status | usage | pause <conn> | resume <conn> | add-connection | import-csv`;
- Telegram: alerts plus `/farm status`, `/pause <conn>`.

**Integrations are added by config:**
1. Add a connection to `registry.yaml`.
2. MCP servers are mounted through the FastMCP proxy plus a tool→capability mapping. REST APIs get a 50–100-line adapter from the template, or are generated from their OpenAPI spec.
3. Every call passes the Resource Manager.

### 4.7 Hermes inside the Farm

- Hermes is a **resource** (`executor: agent`), invoked with the CLI one-shot `hermes -z <brief> --usage-file …` and a role profile.
- **Its only toolset is the Farm MCP:**
  - no terminal or email in production roles;
  - `web.keyless_fallback` / `web.keyless_rescue` set to false;
  - sampling off.
- Budgets go through **Bifrost** virtual keys, plus explicit iteration and time caps (`max_iterations` defaults to unlimited).
- Results come back through `submit_*` tools validated by Pydantic. Prose is ignored.
- Full rules: `docs/HERMES-INTEGRATION.md` §14–15.

### 4.8 Reliability and cost methods (implement all of them)

- Circuit breaker and bulkhead per connection (Nygard, *Release It!*).
- Exponential backoff with jitter.
- Timeouts on every call.
- Reserve → commit/release.
- Idempotency keys.
- Cache first; single-flight.
- Fault-injection tests: 429, auth failure, empty result, timeout.
- Recorded-response tests (respx / vcrpy), so tests never spend real credits.

---

## 5. Farm Console design (decided)

### 5.1 Screens (build in this order)

| # | Screen | Contents |
|---|---|---|
| 1 | Overview | Capacity per capability, spend vs budget + forecast, alerts, health summary |
| 2 | Accounts | Grouped by provider:<br>• used/limit meters, reset date, price, health;<br>• pause/resume toggles;<br>• priority and strategy;<br>• re-login, rotate key, add account. |
| 3 | Billing | Budgets, spend so far this month, forecast, renewal calendar, cost per result, idle accounts |
| 4 | Runs | Every request: routing decisions, fallbacks, cost, evidence links; failure filter |
| 5 | Routing | Capability → resource order (drag to reorder), default strategy per capability |
| 6 | Memory and evidence | Fact browser (entity → facts with source and freshness), screenshot viewer |
| 7 | Integrations | Add an MCP server or API (form → registry), "Test connection" |
| 8 | Policies | Budget caps, hard stops, alert thresholds |

The user's GPT mockups of "Resources" and "Agents" show the intended look: a shadcn/ui SaaS style, dark sidebar, green accent.

### 5.2 Stack

- **App:** Next.js on Vercel, using the shadcn/ui admin starter `arhamkhnz/next-shadcn-admin-dashboard` (no hosted-auth dependency), with `satnaing/shadcn-admin` as the fallback.
- **Components:** TanStack Table, Recharts, RJSF + `@rjsf/shadcn`. **Forms are generated from the same Pydantic JSON Schemas as `registry.yaml`**, so there is no duplicate logic.
- **Data and login:** Supabase JS. Supabase Auth with a magic link, restricted to the owner's email, plus row-level security.

### 5.3 How a cloud dashboard controls a local Farm

- Shared state lives in **Supabase Postgres**: the Farm's own database.
- The Console reads state and **writes commands** into `farm_commands` (pause, resume, priority, budget edits, add connection).
- The local Farm subscribes (Supabase Realtime, or a short poll), validates each command, executes it, and writes the result back.
- **There is no inbound connection to the PC**, no tunnel needed, and **provider keys never leave the PC.** The Console only stores the env-var *name*; the user types the secret into the local `.env` (CLI helper `farm set-secret`).
- Evidence originals stay on the PC. Small thumbnails are uploaded to Supabase Storage (1 GB free) for the viewer.

---

## 6. Open-source materials (verified 2026-10-03; pin these)

**Python (the Farm):**

| Package | Version | Role |
|---|---|---|
| `fastmcp` | 4.0.10 | MCP gateway: composition, middleware, proxy provider, OpenAPI → tools, auth, telemetry, tasks |
| `mcp` | 2.3.0 | Official MCP SDK (FastMCP dependency) |
| `dbos` | 3.2.0 (MIT) | Durable recipes, per-connection queues, dedup |
| `crawl4ai` | 0.9.4 | Website capture: screenshot, HTML, markdown |
| `playwright` | latest | Browser engine for Crawl4AI and the capture service |
| `pydantic` | 2.13.5 | Schemas → JSON Schema (Console forms) |
| `httpx` | 0.28.1 | REST adapters |
| `tenacity` | 9.1.4 | Retries with backoff |
| `aiolimiter` | 1.3.0 | Rate limits |
| `pybreaker` | 1.4.1 | Circuit breaker (or a small own implementation backed by Postgres state) |
| `psycopg` | 3.3.6 | Postgres driver |
| `structlog` | 26.1.0 | Logs |
| `tldextract` / `rapidfuzz` | 5.3.2 / 3.14.6 | Entity keys / fuzzy matching |
| `respx` / `vcrpy` | 0.23.1 / 8.3.0 | Recorded-response tests |
| `typer` | 0.27.2 | CLI |

**Services and binaries:**
- **Bifrost** (maximhq/bifrost, Apache-2.0, Go): LLM gateway with budgets, fallback and keys.
- **Postgres** via a Supabase project.
- **wappalyzergo** (Go, MIT).
- **Hermes** at the pinned commit.

**Reference only, not dependencies:**
- **IBM ContextForge** (gateway federation ideas);
- MetaMCP, Docker MCP Gateway, agentgateway (aggregation patterns);
- LiteLLM (budget and fallback ideas; avoid as a dependency after the March-2026 PyPI incident).

**JavaScript (the Console):**
- `next`;
- the shadcn/ui starter above;
- `@tanstack/react-table` 9.2.4;
- `recharts` 3.10.1;
- `@rjsf/core` + `@rjsf/shadcn` 6.11.0;
- `@xyflow/react` 12.12.0 (later, for the routing graph);
- `@supabase/supabase-js`.

Add the repos to `research/library.tsv` and fetch them with `scripts/fetch-library.sh` in Phase 0, so workers can read real source code instead of guessing APIs.

---

## 7. Team and token plan

| Role | Who | Use for |
|---|---|---|
| Lead | Claude (Opus) | Plan, briefs, gates, diff review, commits, all decisions |
| Bulk builder | **Gemini 3.8 via the Antigravity CLI** (fallback: Gemini CLI) | Modules from briefs: adapters, router, Console screens |
| Mechanical worker | **Qwen3-Coder-Next via Hermes** (`zai.glm-5` for bounded checklist reviews) | Scaffolds, migrations, fixtures, data pulls, repetitive edits |
| Checker | worker-check (Sonnet) + scripts | pytest, ruff, mypy, Playwright UI checks; pass/fail only |
| Taste and UX writing | worker-write or Claude | Console copy, sample review |

**Rules:**
1. One brief = one task of 300–800 lines with exact files, steps, "done when" and a reply format. Template in §12.
2. Builders never commit. The lead commits after the checks pass.
3. Scripts run tests at zero tokens. Anything checked twice becomes a script.
4. The lead never reads large files: diffs, `| tail`, counts only.
5. Run a fresh lead session per phase; the handoff is `.orchestrate/state.md`.
6. Hermes and Gemini briefs start with the safety block from `.claude/skills/orchestrate/references/hermes.md`: touch only listed files; no push, commit, deploy, delete or outbound data.
7. Every non-Claude output is checked before use.

**Token budget (estimates; report actuals at each milestone):**

| Phase | Claude | Gemini | Qwen (Hermes) |
|---|---|---|---|
| Phase 0 pre-setup | ≤ 0.5–1M | ~0 | ~0.2M (downloads, checks) |
| Phase 1 Farm | ≤ 3M | ~6–10M | ~1–2M |
| Phase 2 Console | ≤ 2M | ~4–6M | ~0.5M |

---

## 8. PHASE 0 — Pre-setup (no building, no spending). Time box: 0.5–1 day

### 8.1 Ask the user first, in one message, numbered

1. **GitHub:** is the Claude GitHub App installed on `milvow-ai/Tensor`? Test with a push. Without it, work is lost when the container resets.
2. **Where this session runs:** on their PC (required to run the Farm), or cloud (planning only)?
3. **Claude plan:** Pro or Max. It sets the lead's daily throughput.
4. **Gemini:** the exact Antigravity CLI binary and login (Google account), and the current free quota. Is Gemini CLI an acceptable fallback?
5. **Hermes:** HERMES_HOME path, Bedrock region, and a **spend limit** for this build.
6. **Supabase:** create a new project "harness-farm" (the free plan allows 2 active; 3 are currently paused), or restore one?
7. **Vercel:** the account to deploy to, and which plan.
   - **Verify the plan terms:** Vercel describes Hobby as for personal / non-commercial use, and this Console is meant to be used as a product. Pro is ~$20/user/month. Unverified here.
8. **Accounts for the registry,** per provider:
   - accounts count and plan, price, billing day, allowance, API or MCP-only;
   - especially the **7 Clay accounts**: plan, and does each have API access or only MCP (OAuth)?
9. **Keys:** the user adds them to `.env` on the PC. List the env-var names you need; never ask for values.
   - Apollo, Hunter, Reoon, ZeroBounce, Groq, Google (PageSpeed), Adzuna, Telegram bot token + chat id, Supabase URL + service key (Farm only), Bifrost / Bedrock.
10. **Budgets:** monthly cap per provider plus a global cap, and the alert thresholds.
11. **PC:**
    - Windows version, RAM (16 GB minimum), free disk (~20 GB);
    - WSL2 / Docker Desktop installed? (Bifrost and Postgres tools can run without Docker, but Docker is the simplest.)

### 8.2 Environment setup and checks (after the answers)

Each step needs a checkable result:

1. **Tool check:** `git`, Python 3.12, `uv`, Node 20+, `pnpm`, Playwright browsers, Docker (optional). **Pass** = each `--version` prints.
2. **CLI smoke tests** (tiny prompt each; record the exact flags in `.orchestrate/state.md`):
   - Antigravity / Gemini CLI headless one-shot;
   - `hermes -z "reply OK" --usage-file …`;
   - `claude -p "OK" --output-format json`.

   **Pass** = output "OK" plus a usage file written.
3. **Hermes role profiles:** `farm-builder` (terminal + file + code in the repo worktree) and `farm-agent` (Farm MCP only).
   - Settings: keyless web fallbacks off, sampling off, Docker terminal backend for agent roles, explicit iteration and time caps.
   - **Pass** = a profile dump shows these settings.
4. **Library:** add the §6 repos to `research/library.tsv` and run `scripts/fetch-library.sh`. **Pass** = every repo at its pinned commit.
5. **Repo scaffold** (§11 structure): `pyproject.toml` with the §6 pins, `ruff`/`mypy`/`pytest` config, `console/` from the starter, `.env.example` (names only), `.orchestrate/state.md` from the template.
   - **Pass** = `uv run pytest` (0 tests) and `pnpm build` (starter) both succeed.
6. **Supabase:** project created, connection string in `.env`, migration tooling ready (Alembic). **Pass** = `farm db check` connects.
7. **Bifrost:** run locally with one provider (Groq or Bedrock) and a virtual key with a small budget. **Pass** = a test completion through Bifrost.
8. **Plan:**
   - Write the Phase 1 and 2 plan into `.orchestrate/state.md`: milestones, owners, briefs, checks, time boxes.
   - Show the user ≤ 10 lines.
   - **Wait for "go".**

---

## 9. PHASE 1 — Build Harness Farm (action). Time box: 5–6 working days

| Milestone | Scope | Owner | Done when (checks) | Box |
|---|---|---|---|---|
| **M1 Walking skeleton** | FastMCP gateway + middleware chain; registry loader (YAML → Pydantic); quota ledger with reserve/commit/release; cache; run trajectory. **One capability end to end:** `verify_email` with Reoon → ZeroBounce fallback | Lead spec; Gemini build | pytest green; a recorded test shows Reoon exhausted → ZeroBounce used, reservation released, trajectory written; Claude calls it over MCP | 1 day |
| **M2 Resource Manager** | Health and circuit breaker, cooldowns, rate limits (DBOS queue per connection), ranking, strategies (failover, most_remaining, round_robin, pin). Adapters: Apollo, Hunter, Reoon, ZeroBounce, PageSpeed, Adzuna, Groq/Ollama via Bifrost | Gemini adapters from one template; Qwen fixtures | Fault-injection suite (429, 401, empty, timeout) passes; ranking unit tests; zero live credits spent in tests | 1–1.5 days |
| **M3 Multi-account pool + Account Manager** | N connections per provider; `parallel_split`, `sticky`, `fit_check`; re-queue on failure with idempotency; per-connection OAuth token stores (Clay MCP via FastMCP proxy); balance sync; pacing; budgets + hard stop; renewal reminders; Telegram alerts; `farm` CLI | Gemini; Qwen for CLI and fixtures | Simulated 7-account Clay pool: parallel split of 70 items finishes with no double charge after killing one account mid-batch; budget cap blocks paid calls; reminder fires in a time-travel test | 1.5 days |
| **M4 Knowledge and agents** | Facts + freshness, entity keys, Crawl4AI evidence capture (screenshot, HTML, hash, robots), recipes (`analyze_website`, `find_person`, `find_email`, `research_company`), Hermes `agent_task` resource with the Farm MCP as toolset + `submit_*` validation, policy rules | Gemini; lead reviews the policy | A real `research_company` run on one public site produces facts with evidence ids; a second identical call costs 0 (cache hit); Hermes run validated; a blocked tool call is logged | 1–1.5 days |
| **M5 Hardening** | Windows run guide, `farm doctor` self-check, backups (pg_dump), docs (`docs/FARM-OPERATIONS.md`), Supabase command-queue consumer (needed by the Console) | Qwen docs; Gemini consumer | Clean run on the user's PC; `farm doctor` all green; a command written to `farm_commands` is executed and acknowledged | 0.5–1 day |

Commit after every milestone that passes its checks, and update `.orchestrate/state.md` (status, actual tokens, deviations).

---

## 10. PHASE 2 — Farm Console (dashboard). Time box: 3–4 working days

| Milestone | Scope | Done when |
|---|---|---|
| **C1** | Starter set up with Tensor/Farm branding; Supabase Auth (owner-only); layout and sidebar; **Overview** + **Accounts** (meters, toggles, strategy, priority → `farm_commands`) | Headless Playwright screenshots at desktop and phone widths; a toggle pauses a real connection through the queue |
| **C2** | **Billing** (budgets, forecast, renewals, cost per result) + **Runs** (trajectory viewer with fallbacks and evidence thumbnails) | Data matches SQL views; forecast unit-tested |
| **C3** | **Routing**, **Memory and evidence**, **Integrations** (RJSF forms from the registry schema + "Test connection"), **Policies**; deploy to Vercel | Vercel deployment live behind login; Lighthouse ≥ 90 performance and accessibility on Overview; no secrets in the client bundle (grep the build) |

**Optimisation rules for the Console:**
- server components or static where possible;
- paginate every table;
- SQL views for aggregates (not client-side maths);
- Realtime only on Accounts and Runs;
- no chart libraries beyond Recharts;
- bundle-size check in CI.

---

## 11. Target repo structure

```
farm/                      # Python package (the Harness Farm)
  gateway/                 # FastMCP server, middleware chain, tool definitions
  registry/                # YAML loader, Pydantic models, JSON Schema export
  resources/               # router, strategies, quota ledger, health, account manager, billing
  executors/               # api/ mcp/ llm/ agent/ browser/ local/ human/
  adapters/                # one file per provider (template: adapters/_template.py)
  knowledge/               # facts, entities, evidence, cache policy
  recipes/                 # DBOS workflows: analyze_website, research_company, …
  policy/                  # budget, scope, compliance rules
  control/                 # farm_commands consumer, Telegram, CLI (typer)
  db/migrations/           # Alembic
tests/                     # unit, fault-injection, recorded (cassettes/)
config/registry.yaml       # providers, connections, capabilities, routes, budgets
console/                   # Next.js Farm Console (Vercel)
.orchestrate/state.md      # orchestrate state (lives in the repo)
docs/  research/  memory/  scripts/  library/ (git-ignored)
```

---

## 12. Templates

**`config/registry.yaml` (shape):**
```yaml
providers:
  clay:
    executor: mcp            # api | mcp | llm | agent | browser | local | human
    default_strategy: sticky
    connections:
      - id: clay-01
        auth_ref: token-store:clay-01        # or env:CLAY_KEY_01 on API plans
        scope: [internal]                     # or [client:<id>]
        units: {credits: {limit: 2500, reset: monthly, anchor: 14, charged_on: success},
                actions: {limit: 15000, reset: monthly, anchor: 14}}
        concurrency: 2
        priority: 1
        plan: {name: launch, price_usd: 185, billing_day: 14}
capabilities:
  verify_email: {routes: [reoon-free, zerobounce-free], strategy: failover, cache_ttl_days: 60}
budgets: {global_monthly_usd: 50, per_provider: {clay: 0, reoon: 0}}
```

**Brief for Gemini or Hermes** (`briefs/<id>.md`):
```
Rules for every step: touch only <files>. Never git push/commit, deploy, delete, or send data off this machine.
Goal: <one paragraph>   Read first: CLAUDE.md, HANDOFF.md §<n>, <files>
Steps: 1… 2… 3…          Owns: <files>   Must not touch: <files>
Done when: <exact command> passes   Reply: ≤ 12 lines — files changed, check output tail, open issues
```

---

## 13. Risks to check early

| Risk | Early check | Fallback |
|---|---|---|
| Antigravity CLI has no headless mode | Phase 0 smoke test | Gemini CLI headless one-shot |
| Clay accounts are MCP-only: OAuth per account, no balance API | Phase 0 question 8; M3 spike | Estimated usage + periodic Hermes or manual balance reading |
| Gemini free quota runs out mid-build | Track per milestone | Shift tasks to Qwen via Hermes; the lead writes small pieces |
| Windows quirks (paths, Playwright, Docker) | M5 run guide, `farm doctor` | WSL2 |
| Supabase free limits (500 MB, pauses after 7 idle days) | Evidence stays local; Farm activity keeps it awake | Local Postgres; the schema is the same |
| Vercel plan terms for product use | Phase 0 question 7 | Pro plan, or Cloudflare Pages |
| DBOS multi-process recovery needs executor ids | Set an executor id per worker from M2 | Hatchet behind the same port |

---

## 14. Outside this effort (do not start; listed so nothing is lost)

These belong to the later Tensor outreach effort:
- outreach mailbox decision;
- postal address and sender identity for the email footers;
- milvow.com case-study fix;
- pilot verticals.

See `docs/TENSOR-FOUNDATION.md` "Decisions needed" and `memory/mem0-import.jsonl`.
