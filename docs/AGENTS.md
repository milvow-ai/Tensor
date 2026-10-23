# Harness Farm — agents and the delegation engine (design v1, 2026-10-24)

> **Harness Farm — your own tools farm.** You grow a farm of AIs, accounts, MCPs and CLIs. Any AI you talk to (the operator)
> uses the Farm as its nerve system: it keeps the reasoning for itself and turns other AIs into its agents, each with the right
> tools, the right compute and only the context it needs.

Companion to `docs/ARCHITECTURE.md` (platform, tenancy, scaling) and `docs/PRODUCT.md` (what the user sees). Status labels:
**[exists]** in the code · **[change]** exists but changes · **[new]** to build · **[spike]** to verify before building.

---

## 0. The idea, engineered

### 0.1 What an agent needs, and what the Farm provides
A model alone answers questions. What makes an AI app *capable* is everything around the model. The Farm does not "turn
things into agents"; it is the **operating system for AI work**: it schedules work, gives each worker its resources and
permissions, keeps files and memory, and accounts for every unit used. The AIs stay the brains; the Farm is the body and the
nervous system they share.

| An agent needs | Without the Farm | With Harness Farm |
|---|---|---|
| **Compute** (models, accounts) | One account per app; a limit stops the work | Pools of accounts across AIs, quotas, failover, subscription-first **[exists]** |
| **Tools** | MCP set up again in every app | One catalog per tenant, pooled accounts, risk labels **[exists → change]** |
| **A workforce** (other AIs) | Copy-paste between apps by hand | `delegate` / `do`, agents chosen per role **[new]** |
| **An environment** | The person's own machine | A sandbox per step with only its files **[new]** |
| **Context** | Pasted by hand | Context packs and references **[new]** |
| **Memory** | Each app forgets | Project and person memory shared by every agent **[new]** |
| **Identity and secrets** | Keys copied into every app | Vault and task tokens; keys never inside prompts **[new]** |
| **Permission** | A prompt per tool call in each app | One policy and approval system in the Farm + a client allow profile (§0.2) **[new]** |
| **Budget and accounting** | Invisible | Metered per AI, role and task; budgets with a hard stop **[exists → change]** |
| **Observability** | None | Activity stream, traces, cost **[exists → change]** |
| **Recovery** | Manual | Leases, heartbeats, retries, failover **[exists → change]** |

### 0.2 The operator must not be interrupted
- **Problem (seen 2026-10-24):** unless Claude Code runs with permissions bypassed, it asks the person before every Farm tool
  call. The Farm should feel like the operator's extended hand, not a stream of prompts.
- **Root cause:** the client gates each MCP tool separately.
- **Design:**
  1. **Risk is enforced once, server-side.** Budgets, tool risk labels, approvals and audit live in the Farm (§10), so the client
     does not need to ask a second time.
  2. **"Connect your AI app" applies a permission profile.** For Claude Code that is an allow rule for the Farm's tools in its
     settings (`permissions.allow`, e.g. `mcp__harness-farm__*`; the glob must be anchored to the server name — an unanchored
     `*` is ignored). Equivalent settings for Codex, Cursor and Gemini CLI are verified per client before they ship.
  3. **Fewer, higher-level calls.** The compact surface (`do`, `delegate`, `task_*`) means a whole task costs a handful of calls,
     not dozens.
- **Until the Farm's approvals exist (A2), the profile allows only read-only and delegation tools.** Tools that change data or
  spend in other systems (e.g. pass-through `call_tool` into a server's delete action) keep asking, because nothing server-side
  would stop them yet.

## 1. Vocabulary

| Term | Meaning |
|---|---|
| **Operator** | The AI the person talks to: a Claude Code session, Codex, Gemini CLI, Cursor, any MCP client. It owns the reasoning and the conversation with the person. |
| **Agent** | One AI account running inside an agent runtime, working on one step. Examples: claude-02 on Opus, codex-01, Gemini via agy, DeepSeek V4 via an API key. |
| **Runtime** | How an agent runs: a CLI one-shot, an ACP session, or an API model inside an agent harness (§4). |
| **Task** | What the operator asked for (`do(goal)`); it has a plan, steps, files, a budget and a final report. |
| **Step** | One assignment to one agent: role, inputs, acceptance check, budget, outputs. |
| **Context pack** | The small, exact brief an agent receives instead of the operator's conversation (§6). |
| **Check** | A script (tests, lint, types, build) or a reviewer agent that decides whether a step is done. |
| **Project** | A working folder or repository the task acts on; the unit for shared memory and warm sessions. |

## 2. One task, end to end (the person's example)

The person works in Claude Code (session 1) in a folder for a food-delivery app and says:
*"/harness-farm — build the backend from flowchart.png in Python, use whatever you need."*

1. **Discover.** The operator calls `farm_guide` and sees this person's farm: agents (claude-02 Opus, codex-01, Gemini via
   agy, DeepSeek V4 via API), tools (GitHub, Figma, Sentry, Postgres…), budgets and limits. The Farm also says which missing tools
   would help ("a Postgres MCP would let agents check the schema — not connected") and the operator files
   `request_integration` **[exists]**; the person connects it with one click in the dashboard.
2. **Plan.** The operator keeps the reasoning. It either plans the steps itself and calls `delegate` per step, or calls
   `do(goal, project, mode="team")` and lets the Farm's planner propose the plan (§3). Either way the plan is stored with the
   task and visible in the dashboard.
3. **Assign.** The Farm picks an agent per step (§5): API design → the operator or an Opus agent; implementation → DeepSeek V4
   (cheap, strong at code); tests → Codex; review → Gemini (a different model family from the builder); fixes → back to the
   cheap coder, escalating to Codex or Opus after two failed checks.
4. **Work.** Each agent runs in its own sandbox on a checkout of the project, receives a context pack (§6) — not the operator's
   conversation — and calls Farm tools with a task token that allows only the tools its step needs.
5. **Check.** The Farm runs the scripted checks inside the sandbox (tests, lint, types). Passing costs no tokens. Only a failure,
   trimmed to the relevant lines, goes back to the same agent's warm session, or to a stronger agent.
6. **Merge.** Each step returns a patch, never a whole file dump. One writer per file per step; the Farm applies patches to the
   project branch and re-runs the checks; a conflict is reassigned, never auto-resolved by guessing.
7. **Report.** The operator receives a compact result: what changed, checks passed, open questions, and the cost split
   ("DeepSeek 61% of tokens / 18% of cost, Codex 22% / 31%, Gemini review 9% / 6%, operator planning 8% / 45%").
   The operator's own context stays small.
8. **Remember.** The Farm writes project memory: decisions, conventions, where things are, what failed and why. The next task's
   context packs start from it.

Any MCP client can be the operator: Claude, Codex, Gemini, Cursor. They all call the same Farm with their own tokens.

## 3. Delegation modes (the operator's tools)

| Tool | Who plans | Use | Status |
|---|---|---|---|
| `ask(agent?, question)` | — | A quick answer or second opinion | `ask_ai` **[exists]** |
| `delegate(role or agent, task, refs, checks, budget)` | The operator | One step, operator in full control | `ai_start` **[exists → change]**: add tools, packs, checks |
| `do(goal, project, mode="single"\|"team", budget)` | The Farm planner | Single step (A1) or a plan of steps (team, A2) | **[new]** |
| `task_status` / `task_result(detail)` / `task_reply` / `task_cancel` | — | Follow, read in layers, continue, stop | on top of `ai_status/ai_result/ai_reply/ai_cancel` **[exists → change]** |
| `agents()` | — | This tenant's agents: strengths, prices, capacity, track record | `list_ais` **[exists → change]** |

- **Delegation depth is 1.** A task token can never call `do` or `delegate`. Agents can ask the Farm for tools and memory, not
  for more agents. The operator stays the only conductor.
- **The planner** is an AI step with a fixed JSON schema (steps, roles, dependencies, checks, budget) plus deterministic rules
  (capacity, budget, tenant policy). A cheap model drafts; rules validate; the operator can edit the plan before it runs.
- **Long tasks** use the Farm's own task tools, which every MCP client understands. MCP's async "Tasks" primitive (spec
  2025-11-25) is still experimental and moving to an extension; the Farm adds it when the major clients support it.

## 4. How any AI becomes an agent (runtimes)

| Runtime | For | How the agent gets Farm tools | Session | Status |
|---|---|---|---|---|
| **CLI one-shot** | Claude Code, Codex, Gemini CLI, agy, Hermes | Farm MCP config + task token passed at launch (e.g. Claude `--mcp-config`); today workers get no tools (`--allowedTools ""`, `--toolsets ""`) | `--resume <session>` per project **[exists]** | **[change]** |
| **ACP session** | Agents that speak the Agent Client Protocol: Gemini CLI (`--acp`), Codex (`codex-acp` adapter), Claude Code (`claude-agent-acp` adapter) | The Farm is the ACP client: it passes its MCP endpoint when the session starts; the agent's permission requests become Farm policy checks and approvals | Kept warm per project: no re-reading the project, lower latency | **[spike]** then **[new]** |
| **API agent** | Any API model: DeepSeek, Qwen, GPT, Gemini API, Claude API | The model runs inside an agent harness that has an MCP client — Hermes today (works with OpenRouter models) — with the Farm MCP as its only toolset; keys and budgets through Bifrost virtual keys | Harness session per project | **[spike]** then **[new]** |
| **Human** | Approvals, judgment calls | Dashboard and Telegram | — | approvals **[new]** |

ACP was designed for editors, with a person watching. The spike must prove three things before it is relied on: headless use
from the Farm, MCP endpoint hand-over, and a clean permission flow for each of the three agents.

**Agent profile** (per tenant, seeded from a catalog Milvow maintains): what it is good at (reasoning, code, review, research,
vision), context window, price per million tokens (input / cached input / output), typical speed, limits and reset windows, and
its measured track record per role (pass rate, cost per passed step). Track records come from the tenant's own runs.

## 5. Choosing the agent for a step

Inputs: role, estimated difficulty, budget left, latency need, tenant preferences and pins, live capacity (quota ledger, leases,
health) **[exists]**, and the track record.

1. **Cheapest agent that meets the quality floor for the role**, among agents with capacity. The floor is a pass rate from the
   tenant's history (a default from the catalog until there is history).
2. **Use capacity you already pay for first.** Subscription windows that reset soon go before paid API tokens — the existing
   "free first, soonest reset first" ranking, applied to AI accounts **[change]**.
3. **Review by a different family** than the builder, so the two do not share the same blind spots.
4. **Escalate on failure.** Same check failing twice on a cheap agent → hand the step, with the failure, to the next stronger agent.
   Trying cheaper models first and escalating only when needed is the "LLM cascade" idea (FrugalGPT, Chen, Zaharia & Zou, 2023).
5. **The operator can pin** an agent for any step.

## 6. Token efficiency — the levers

| # | Lever | What it saves | Where |
|---|---|---|---|
| 1 | **Context packs, not transcripts**: goal, acceptance check, constraints, relevant paths, project memory; capped size | The operator never forwards its conversation | planner **[new]** |
| 2 | **References, not contents**: agents read files themselves inside the sandbox | Nothing large passes through the operator | sandbox **[new]** |
| 3 | **Compact results**: a report of 15 lines or fewer + artifact links; details on request (`task_result(detail=…)`) | The operator's context stays small | task tools **[new]** |
| 4 | **Warm sessions** per agent × project (resume / ACP), compacted into a summary when they grow long | Agents don't re-read the project for every step | `ai_sessions` **[exists → change]** |
| 5 | **Cache-friendly prompts**: stable part first (rules, project pack), changing part last | Providers bill cached input far below normal input (rates differ; read from the price table) | packs **[new]** |
| 6 | **Scripted checks**: tests, lint, types run as code; only trimmed failures go back | Verification costs no tokens | sandbox **[new]** |
| 7 | **Cheapest capable agent + escalation** (§5) | Expensive models only where needed | selector **[new]** |
| 8 | **Result cache and dedupe** for repeatable tool calls | Never pay twice | router cache **[exists]** |
| 9 | **Budgets with early stop**; the same failure twice stops or escalates | No runaway loops | task layer **[new]** |
| 10 | **Parallel only when independent**; one writer per file | No duplicated work or merge fights | planner **[new]** |

The engine does not promise a saving in advance. It **measures** it (§8): what the task cost, and what the same work would have
cost on one reference model.

## 7. Memory

| Layer | Scope | Stored in | Written by | Used by |
|---|---|---|---|---|
| **Working** | One step | Context pack in the task row | Planner | The agent |
| **Agent session** | Agent × project | The agent's own session (resume id / ACP session) | The agent | The same agent next time |
| **Project** | Tenant × project | `memory_items` (Postgres; vector search added when keyword search is not enough) | Agents after each step (`memory_add`), the operator, the person in the dashboard | Planner → top items into packs |
| **Person / workspace** | User or tenant | `memory_items` with that scope | The person, the operator (with approval if the policy says so) | Planner |
| **Files and evidence** | Tenant | Object storage + index | Agents (artifacts) | All, by reference |

Rules: every memory has a source (who, when, which task), a confidence and an optional expiry; duplicates are merged;
contradictions are flagged, not silently overwritten; each pack has a memory size cap; the person can see, edit and forget
anything in the dashboard. Memory is tenant data under RLS. `MemoryPort` keeps an external memory service (mem0) possible as an
adapter; the default is native.

## 8. Cost and efficiency accounting

Every agent call is metered **[change]** — the CLIs already report usage: Claude Code (`usage`, `total_cost_usd`), Codex
(`turn.finished` usage), agy (`usage`), Hermes (usage file with `estimated_cost_usd`) **[exists]**.

- **Recorded per call:** input, cached input, output and reasoning tokens; actual spend (API keys) and notional value
  (subscription use priced at API rates); duration; outcome; attributed to tenant, user, project, task, step, role, agent, model.
- **Price table** per model (input, cached input, output per million tokens), versioned with its source and date, editable per
  tenant.
- **Shown to the person** (dashboard "Cost & Efficiency", see `docs/PRODUCT.md`):
  - **AI mix**: % of tokens and % of cost by AI and model;
  - **Role mix**: % for planning, coding, review, tests, research;
  - **Cache hit rate**; **waste** (tokens spent on failed attempts);
  - **Cost per successful task**; **subscription use** (% of each window used before it resets);
  - **Savings vs baseline**: the same tokens priced as if one reference model had done everything.

## 9. Performance

- **Start fast:** a warm pool of ready sandboxes per image; warm agent sessions (§4); each tenant's tool catalog cached in
  memory and refreshed on change (Postgres LISTEN/NOTIFY).
- **Fair under load:** leases for account and tenant limits; a fair queue per tenant so one tenant cannot starve others;
  workers scale out with queue depth.
- **Robust:** every step is idempotent (patch-based) and resumable; heartbeats and leases recover from crashed workers; retries
  with backoff; a dead-letter list for steps that keep failing.
- **Initial targets for 10–20 users (to measure, not promises):** Farm overhead per tool call p95 < 150 ms excluding the
  provider; task start p95 < 10 s with a warm pool; zero cross-tenant leaks; 99.5% monthly availability of the API.

## 10. Safety

Delegation depth 1 · task tokens with tool allow-lists, budgets and expiry · sandboxes with no host filesystem · secrets never
inside a context pack or a report · risky tools pause for approval (ACP permission requests and MCP tool annotations feed the
same policy) · every decision written to the audit log.

## 11. What exists and what changes (code map)

| Today | Becomes |
|---|---|
| `farm/ai/jobs.py` JobManager (queue, slots, retries, cancel, heartbeat leases) **[exists]** | The step executor on the global queue and leases (F2) |
| `ask_ai`, `ask_ai_batch`, `ai_start…ai_conversations`, `list_ais` **[exists]** | `ask`, `delegate`, `do`, `task_*`, `agents` |
| CLI drivers `farm/executors/cli_agent/{claude,codex,agy,gemini_cli,hermes}.py` **[exists]** | + Farm MCP with task token; + ACP driver; + API-agent runtime |
| Usage parsing per CLI **[exists]** | Token types, notional vs actual cost, role attribution |
| `ai_sessions` / conversations **[exists]** | Warm sessions per agent × project, with compaction |
| Router, ledger, health, ranking **[exists]** | Agent selection: profiles, quality floor, escalation |
| `request_integration` **[exists]** | Task-aware tool recommendations |
| — | Planner, context packs, checks in sandbox, patch merge, memory, approvals, cost analytics **[new]** |

## 12. Spikes before building (S0, 0.5–1 day each)

1. **ACP:** drive Gemini CLI (`--acp`), `codex-acp` and `claude-agent-acp` headless from Python; hand over the Farm MCP endpoint;
   observe permission requests; measure warm-session latency and token use against CLI one-shots.
2. **API agent:** Hermes running DeepSeek V4 with the Farm MCP as its only toolset on a small coding step; measure pass rate and
   cost against Codex and Claude on the same step.
3. **MCP injection for one-shots:** start Claude Code, Codex and agy with a Farm MCP config and a task token; confirm each sees
   only the allowed tools.
4. **Metering:** confirm token types and cost fields each runtime really reports, live, not from docs.

Each spike ends with a one-page result in `docs/spikes/` and an ADR update in `docs/ARCHITECTURE.md`.
