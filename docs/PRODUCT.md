# Harness Farm — product and dashboard (design v2, 2026-10-24)

> **Harness Farm — your own tools farm.** Grow a farm of AIs, accounts, MCPs and CLIs. Whichever AI you talk to uses it as
> its nerve system: it keeps the thinking and gets agents, tools and compute on demand — and you see exactly what each AI did
> and what it cost.

v2 replaces v1, which listed screens from the builder's side. This version starts from what a user needs to know and do, and
borrows proven patterns from products that do one part of this well (§9). Companions: `docs/ARCHITECTURE.md` (platform) and
`docs/AGENTS.md` (delegation engine). Scope marks: **[Gate A]** first 10–20 users · **[later]**.

---

## 1. The user and their jobs

The user is a builder or agency member who works inside AI apps (Claude Code, Codex, Cursor, Gemini) all day. They open the
dashboard to:

| # | Job | Answered by |
|---|---|---|
| 1 | **Set up once** — connect my AIs, accounts, tools, and my AI apps | Onboarding, Farm, Settings → AI apps |
| 2 | **See what happened** — what my AIs did, with which tools and agents | Activity, Tasks |
| 3 | **Know what it cost** — which AI did what, where money and tokens went, what was wasted | Usage & Cost |
| 4 | **Trust my farm's health** — which accounts are ready, limited or need me, and *why* | Farm, Overview |
| 5 | **Stay in control** — approvals, budgets, who may use what | Approvals, Settings → Policies |
| 6 | **Shape what agents know** — project and personal memory | Memory |
| 7 | **Run the team** — members, tokens, audit | Settings |

## 2. Design principles

1. **Calm when healthy, loud when not.** Neutral by default; colour only means state (green ready, amber attention, red broken).
   Nothing blinks for decoration.
2. **Every status shows its evidence.** "Needs login" always says what was observed, from which run, when it was checked, with
   a **Re-check now** button. A status is never shown without its reason (the false "quota reached" flags of October came from
   statuses without evidence — `docs/QUALITY.md`).
3. **Every number drills down** to the rows behind it: a chart point → the activity behind it → the trace of one call.
4. **One activity backbone.** Everything any person or AI did lives in one stream with filters and saved views; every other
   page is a filtered view of it.
5. **Objects have one canonical page with the same anatomy:** header (name, state, key numbers) → tabs (Overview · Activity ·
   Usage · Settings). AI accounts, tools, tasks, projects and memories all follow it, so the product is learned once.
6. **Keyboard-first and fast:** ⌘K command palette for every object and action, `g` + letter to jump, optimistic updates, live
   streams for running work.
7. **Honest numbers:** actual spend and notional value (subscription use priced at API rates) are always separated; estimates
   are labelled as estimates.
8. **Every state designed:** loading skeletons, empty states that teach the next step, errors that say what to do.

## 3. Visual language (direction for the mockups)

The owner's visual reference governs the look (2026-10-24): `docs/design/reference-dashboard.png`, turned into tokens,
components and page compositions in `docs/design/DASHBOARD-COMPONENTS.md`. Where this section and that file differ, that file
wins.

- **Type:** Geist Sans for UI, Geist Mono for ids, numbers and code; tabular figures in every table and metric.
- **Theme:** light-first like the reference (light grey canvas, white cards, hairline borders), with full dark-theme parity; a
  neutral grey ramp; **one blue accent** used only for primary actions, selection and the main series; a separate semantic
  set for state, so the accent never reads as "healthy".
- **Layout:** 8-pt grid; collapsible left sidebar; analytics pages up to 1440 px wide; tables at 36–40 px rows with sticky
  headers; drawers for detail so the list stays in view.
- **Charts:** stacked area for usage over time, bar lists for rankings, a donut only for mix (six segments + "other" at most),
  sparklines in metric tiles; no 3D, no gradients on data.
- **Motion:** 150–200 ms ease-out for state changes only.
- The design system (tokens, components) is set in the mockup round; the current Console's shadcn/ui components are the base.

## 4. Navigation

```
[Workspace ▾]                         ⌘K  ·  period: Last 7 days ▾  ·  ● live
  Overview
  Activity
  Tasks
  Farm          AI · Tools · APIs
  Usage & Cost
  Memory
  Approvals  (3)
  ─────────
  Settings      Workspace · Members · AI apps & tokens · Policies & budgets · Prices · Audit log · Notifications
  Docs · Status · [user]
```
The period selector is global and kept in the URL, so every view can be shared or saved.

## 5. Pages

### 5.1 Overview — "is my farm healthy, what did it do, what did it cost, what needs me?" [Gate A]
```
┌ Needs you (only when something does): 2 approvals waiting · agy-01 needs a login · budget at 82% ┐
├ Tool calls 1,284 ▲12% │ Tasks done 37 ▲4 │ Spend $18.40 ▼9% │ Saved vs baseline $61 │ Success 94% ┤  ← tiles: value, sparkline, Δ vs previous period
├ Usage over time (stacked by AI; tokens / cost / calls)          │ Farm health                     ┤
│                                                                │  11 ready · 3 limited · 1 needs you │
│                                                                │  agy-01 — needs login (why ▸) [Re-check] │
├ AI mix (donut)         │ Top tools (bar list)         │ Subscription windows (used %, resets in) ┤
├ Recent activity (live, last 15)                                 │ Recently learned (memory, 5 items)  ┤
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
New workspace: the page is an onboarding checklist instead (connect an AI · connect a tool · connect your AI app · run a first
task), which stays until all four are done.

### 5.2 Activity — everything, in one stream [Gate A]
- **Row:** time · actor (person + AI app, e.g. "Amaan via Claude Code") · action · target (tool / agent / account) · outcome ·
  duration · cost · project.
- **Covers:** tool calls, delegations and steps, account state changes (with evidence), connections added or removed,
  approvals, settings changes, errors.
- **Filters:** actor, AI app, agent, tool, project, outcome, cost above; full-text search; **saved views** ("Failures today",
  "Spend over $1", "Clay calls"); live toggle; CSV export.
- **Detail drawer:** the call's **trace tree** — request → policy → candidates → reserve → execute → commit — with timings;
  inputs and outputs collapsed and redacted; cost split; links to the task, account and tool; replay for read-only calls.

### 5.3 Tasks — delegated work [Gate A]
- **List:** tabs Running · Waiting for you · Done · Failed; columns goal, operator, agents, step progress, cost, duration.
  Bulk cancel and retry.
- **Task page:** goal, status, budget bar · **plan graph** (steps and dependencies, each with its agent and state) ·
  **timeline** of steps and spans · step drawer: the context pack sent, tool calls, check results, diff viewer, report ·
  cost by agent and by role · files · inline approvals · actions: approve, retry a step on another agent, cancel, continue.

### 5.4 Farm — "your farm" [Gate A]
- **Index:** tabs AI · Tools · APIs. A card per resource: name, type (API key, CLI login, MCP), pool health ("3 of 4 accounts
  ready"), usage this period, last used, alerts. Primary action **Add to farm** opens a catalog: search, categories, how it
  connects (OAuth, key, CLI login via runner), what it enables.
- **Resource page** (e.g. Claude, Clay):
  - header: state with evidence, accounts, strategy;
  - **Accounts table:** label, auth type, state badge with a **"why"** popover (observed error excerpt, source run, checked
    at) and **Re-check now**, window usage bar with "resets in", calls today, success rate, actions (pause, resume, priority,
    re-login);
  - **Tools tab** (MCP): every tool with its risk label (read-only / changes data / may spend), on/off switch, calls, latency;
  - Usage and Activity tabs (filtered views); Settings tab: strategy, priority, budgets, the roles this AI may take as an
    agent, parallel limit.
- **Requests:** tools an AI asked for during a task ("needed Postgres access"), each with one-click connect.

### 5.5 Usage & Cost — which AI did what, and what it cost [Gate A]
- **Group by:** AI · model · tool · project · person · AI app · role; global period.
- **Tiles:** actual spend · notional value · savings vs baseline · cache hit rate · cost per successful task · waste.
- **Charts:** spend / tokens over time stacked by the chosen group; AI mix and role mix; **Trends** (what rose and fell most
  versus the previous period); **subscription use** per account (window, % used, resets, value left unused); **budgets**
  (spent, forecast to month end, status).
- **Table:** per group — calls, tokens (input / cached / output), actual spend, notional value, success rate; every row drills
  into Activity with the same filter. CSV export.
- Definitions shown on hover (e.g. "Savings vs baseline: the same tokens priced at the reference model set in Settings →
  Prices").

### 5.6 Memory — what agents know [Gate A]
- Scopes on the left (workspace · people · projects); items in the centre (text, tags, source agent and task, created,
  confidence, last used); detail drawer with provenance and use ("used in 7 context packs"), edit, pin, forget (audited).
- **Preview pack:** exactly what an agent would receive for a project right now.

### 5.7 Approvals [Gate A]
- Queue of cards: what (tool, action, target), why (the agent's stated reason), who (operator, agent), cost so far, risk
  label; approve or deny with a note; keyboard `A` / `D`. Same queue on Telegram.

### 5.8 Settings [Gate A]
- **AI apps & tokens:** for Claude Code, Codex, Cursor and Gemini CLI — a ready config with the person's token, a **permission
  profile** (for Claude Code an allow rule for the Farm's tools, so the person is not asked before every call; risk is enforced
  by the Farm's own policy and approvals — `docs/AGENTS.md` §0.2), and **Test connection**. Personal tokens: name, scopes,
  last used, revoke.
- Workspace · Members (invite, roles) · Policies & budgets · Prices (per model, with source and date) · Audit log ·
  Notifications · Data export and deletion.

### 5.9 Admin — Milvow staff only [Gate A]
Tenants (plan, usage, health) · cells and roles (queue depth, lease waits, error rate) · limits · support access (audited;
secrets never visible) · feature flags.

## 6. First ten minutes [Gate A]

Sign in with Google → profile (name, role, time zone) → create or join a workspace → **Add to farm**: an AI (API key) and a
tool (OAuth) → **Connect your AI app** (copy config, apply the permission profile, test) → run the guided first task → land on
Overview with real numbers.

## 7. Beta limits [Gate A]

Invite-only. Per workspace: tasks per day, parallel tasks, storage, connected accounts — each a setting, so raising a cap is
configuration. Free during the beta; metering runs from day one so pricing comes from real data.

## 8. Engineering of the dashboard

- **Fast analytics at any scale:** charts read **rollup tables** (per tenant × hour/day × dimension) kept current by the
  scheduler, never large `GROUP BY`s over raw events at request time. Targets: Overview interactive < 2 s; dashboard queries
  p95 < 300 ms.
- **Live:** server-sent events from the `api` role for Activity, Tasks and Approvals (Supabase Realtime until then).
- **Client:** Next.js App Router, TanStack Query (stale-while-revalidate), filters and saved views in the URL, TanStack
  Virtual for long tables, shadcn/ui + Recharts.
- **Access:** tenant-scoped views under RLS; writes only through commands; no secret ever reaches the browser.
- **Quality:** WCAG 2.2 AA; Playwright on the first-ten-minutes journey and every page's empty, loading and error states;
  Lighthouse ≥ 90 on Overview and Tasks.

## 9. Patterns we borrow, and from whom

| Pattern | Learned from |
|---|---|
| Metric tiles with sparkline and change vs previous period; spend by model; drill-down to single requests; saved views; a Trends view; group by model / key / member; export | OpenRouter Activity |
| Hierarchical trace of each run with timings; tags; bulk replay and cancel | Trigger.dev (open source, Apache-2.0) |
| Cost by model, user and session; LLM traces | Langfuse (open source, MIT core) |
| Tool catalog filtered by role and permission; per-user and per-team access; identity-provider sign-in | Obot (open-source MCP gateway) |
| Browsing servers, tools and tool groups | MCPJungle |
| Restraint, colour as state, keyboard-first, command palette, dark-first | Linear, Vercel |
| Usage and cost dashboard components | Tremor (open source) |

We learn the patterns; we copy code only where the licence allows it and it meets the quality bar.

## 10. What happens to today's Console

Reused as the base — components, data layer, charts, forms, tests — restructured:

| Today | Becomes |
|---|---|
| Overview | **Overview** (rebuilt around §5.1) |
| Pools → AI, AI jobs | **Farm → AI**; jobs become steps in **Tasks** |
| Pools → Tools, Integrations | **Farm → Tools / APIs** |
| Runs | **Activity** (trace drawer) and steps inside **Tasks** |
| Billing | **Usage & Cost** |
| Routing | Settings tab of each **Farm** resource |
| Memory | **Memory** |
| Policies | **Approvals** + **Settings → Policies & budgets** |
| Settings | **Settings** |
| — | Sign-in with Google, onboarding, ⌘K, Admin (new) |

## 11. Next design step

Round 0 (taste first): high-fidelity mockups of **Overview**, **Farm → AI → Claude** and **Usage & Cost** in the reference
style (light first, then dark), composed from `docs/design/DASHBOARD-COMPONENTS.md` §4, for the owner's yes before the full set
and before any build.
