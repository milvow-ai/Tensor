# Brief C1 — Farm Console core: shell, auth, data layer, Overview, Tools & Pools, AI Pools

Rules: follow `briefs/CONTEXT.md` §6. Touch only the files under "Owns". Never git add/commit/push, never deploy. Never read `.env` or `console/.env.local`.

## Goal
The owner's control room for the Harness Farm: a real SaaS-grade dashboard (not a demo) where they see every tool pool and AI pool, every
account in it, its usage/limits/billing/health, and can pause/resume, reprioritise, change strategy and add accounts. The Console never holds
provider secrets and never talks to the Farm directly: it reads Postgres (Supabase) and writes commands into `farm_commands` (HANDOFF §5.3).
Design bar: calm, dense, information-first SaaS (Linear/Vercel-dashboard feel): dark sidebar, green accent (`#22c55e` family) on a neutral
palette, light + dark themes, every table paginated, every state designed (loading skeletons, empty, error, needs-login), numbers right-aligned
with units, relative times with absolute on hover, keyboard-focusable controls, WCAG AA contrast. No lorem ipsum, no stock demo widgets.

## Read first
`briefs/CONTEXT.md` (§1, §3 = your data contract), `HANDOFF.md` §5, `console/AGENTS.md`, `console/README.md`, `console/package.json`,
`farm/db/migrations/versions/*` (the real schema), `config/registry.yaml` (the real pools: 7 Clay accounts, Reoon, ZeroBounce, Apollo, Hunter;
AI pools Claude ×3, Gemini, Codex, Hermes). Supabase SSR auth docs for Next.js App Router (`@supabase/ssr`).

## Owns
- Everything under `console/` except `console/.env.local` (the lead created it: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `FARM_DATA_SOURCE=fixtures` for local dev).
- `farm/db/migrations/versions/0003_console_views.py` (down_revision = current head: run `uv run alembic -c <M1a's ini> heads`) and `tests/test_console_views.py`.

## Steps
1. **Strip the starter:** delete every demo route/component/data not used by the Farm (all of `dashboard/*` demo pages, `(legacy)`, `chat`, `mail`, `academy`, …). Keep the layout shell, sidebar, theme/preferences system and `components/ui`. Rename the app to "Harness Farm" (metadata, logo text, favicon text mark). Remove unused dependencies.
2. **Navigation** (sidebar groups): *Operate*: Overview, Tools & Pools, AI Pools, Runs · *Control*: Routing, Policies & Budgets, Billing · *Data*: Memory & Evidence · *Setup*: Integrations, Settings. Pages not in C1 render a designed "Arrives in C2/C3" state that explains what the page will do.
3. **Data layer** `console/src/lib/farm/`: TypeScript types mirroring CONTEXT §3 + the views; a `FarmData` interface (getOverview, listPools(kind), getPool(id), listConnections(poolId, page), getConnection(id), listRecentRuns(n), listAlerts, listCommands(n), enqueueCommand(kind, payload)) with two implementations selected server-side by `FARM_DATA_SOURCE`:
   - `supabase` (default): `@supabase/ssr` server client with the user's session; reads the `v_*` views/tables; `enqueueCommand` inserts into `farm_commands` (RLS lets the owner insert only there).
   - `fixtures`: realistic JSON fixtures (the real registry pools above with plausible usage, health states incl. one open circuit, one cooldown, one needs_login, alerts, 40 runs) and an in-memory command queue that marks commands `done` after 1.5 s. **Allowed only when `NODE_ENV !== "production"`** — throw at startup otherwise.
4. **Auth (supabase mode):** `/login` magic-link page; middleware redirects unauthenticated users; after login, a server check that the email equals `farm_settings.owner_email`, else `/unauthorized`. Fixtures mode skips auth.
5. **Views migration 0003:** `v_connection_status` (connection + provider + health + current-period quota per unit: used, reserved, limit, remaining, next reset), `v_pool_overview` (per provider: kind, accounts total/active/paused/needs_login/exhausted/open-circuit, remaining capacity summary), `v_spend_month` (per provider and total: spend this month from usage_events + billing_events, budget, forecast = spend ÷ elapsed days × days in month), `v_capability_capacity` (per capability: pools in route order with remaining), `v_recent_runs` (runs + final connection + attempts count). PG16-compatible; `security_invoker = true` on views. `tests/test_console_views.py` seeds a pool and asserts each view's numbers (uses the M1a `pool` fixture).
6. **Overview page:** KPI row (pools healthy/total, accounts active/total, spend this month vs budget with forecast, open alerts), capacity per capability (horizontal bars, Recharts), pool health grid (one tile per pool: name, kind, account dots coloured by state), recent runs (last 8: capability, result, pool/account, cost, time → link), alerts list with ack (command `ack_alert`), a "how a request flows" capability → pools → accounts map built from routes (simple columns, no new chart lib).
7. **Tools & Pools page** (`/pools/tools` + `/pools/[id]`): pool cards with strategy + totals; pool detail = accounts table (TanStack Table, paginated, sortable): status badge, per-unit usage meter (used/limit, reserved shown as hatched segment), next reset, plan price & billing day, health (circuit, last error kind, cooldown countdown), priority (inline number), pause/resume switch, row actions. Pool header: strategy select (failover, most_remaining, round_robin, parallel_split, sticky, fit_check), "Add account" dialog (zod form: id, label, auth reference **as an env-var name only** with helper text "the secret stays in the PC's .env — run `farm set-secret <NAME>`", plan name/price/billing day, units with limit/period/anchor/charged_on). Every mutation → `enqueueCommand` with optimistic "queued → done/rejected" feedback (toast + row state) and Realtime/poll refresh on this page only.
8. **AI Pools page** (`/pools/ai` + `/pools/ai/[id]`): same table pattern for AI providers: account, CLI type, models, login state (needs_login shows the exact command the owner runs, e.g. `farm ai login claude-02`), limit state (cooldown until reset with countdown), sessions count, today's calls; "Add AI account" dialog (cli type claude|codex|agy|hermes, id, models).
9. **Quality gates:** add devDeps `@playwright/test` (browsers via env `PLAYWRIGHT_BROWSERS_PATH=D:\dev-cache\ms-playwright`). Scripts in `console/package.json`: `check` = biome lint + `tsc --noEmit` + `next build`; `test:e2e` = Playwright in fixtures mode against `next start`: (a) every nav page renders without console errors at 1440×900 and 390×844 and saves screenshots to `D:\dev-cache\shots\C1\<page>-<w>.png`; (b) pausing a Clay account shows "queued" then "done" and the row becomes Paused; (c) Add-account dialog validates (rejects a value that looks like a secret, e.g. starts with `sk-`), submits, command appears; (d) no horizontal scroll at 390 px. `bundle` script fails if any route's first-load JS > 250 kB (parse the `next build` output).

## Done when
`pnpm check` and `pnpm test:e2e` pass in `console/`; `scripts/check.ps1` passes (views test); screenshots exist for every page at both widths.

## Reply (≤ 15 lines)
Files/areas changed; deleted starter areas; check + e2e tails; screenshot folder; bundle sizes per route; deviations and open issues.
