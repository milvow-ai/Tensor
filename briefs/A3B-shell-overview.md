# A3B — Dashboard v2: shell + Overview (builder: agy-01, Gemini 3.8 Flash High)

Worktree `D:\Harness Farm\wt-a3`, branch `a3-dashboard`. Console = Next.js in `console/`. Data = fixtures mode in dev/e2e
(`console/src/lib/farm/fixtures/*`) and Supabase views in production, both behind `console/src/lib/farm/*` loaders.

## Read first (only these)
- `docs/PRODUCT.md` §2 principles, §3 visual language, §4 navigation, §5.1 Overview, §8 engineering, §10 mapping.
- `docs/design/DASHBOARD-COMPONENTS.md` (the approved component set; owner approved the Round 0 look on 2026-10-24).
- `console/src/components/kit/*` — the approved UI kit. **Build pages from these components; do not invent a new look.**
  Live gallery: `/dev/components`.
- Today's `console/src/app/(main)/layout.tsx`, `console/src/app/(main)/overview/*` and the current sidebar component.

## Build
1. **Shell:** the v2 navigation of PRODUCT §4 using `kit/AppSidebar`: Overview · Activity · Tasks · Farm (AI · Tools ·
   APIs) · Usage & Cost · Memory · Approvals · Settings. Link targets: Overview `/overview`, Activity `/runs`, Tasks
   `/tasks`, Farm → AI `/pools/ai`, Tools `/pools/tools`, APIs `/integrations`, Usage & Cost `/usage`, Memory `/memory`,
   Approvals `/approvals`, Settings `/settings`. Top bar: `kit/CommandSearchButton`, `kit/PeriodSelect` (period kept in the
   URL as `?period=7d`, default 7d), live dot, `kit/NotificationsBell`. Keep the signed-in user card at the bottom.
   Pages that do not exist yet (`/tasks`, `/approvals`) get a minimal page with `kit/PageHeader` + an honest empty state
   ("Arrives with delegation (A1)").
2. **Overview** rebuilt around PRODUCT §5.1 with kit components (`MetricCard`, `TrendChartCard`, `BreakdownCard`,
   `InsightsPanel`, `StatusChip`, `MeterBar`, ...), fed only by data the existing loaders already return. A number the
   Farm does not record yet is left out, never faked.
3. Alerts: show only **open** alerts from the last 24 h by default, newest first, with "show older"; the 43 old watchdog
   restarts must not dominate the page.

## Files you own
`console/src/app/(main)/layout.tsx`, the sidebar/top-bar components it uses, `console/src/app/(main)/overview/**`,
new `console/src/app/(main)/tasks/**` and `console/src/app/(main)/approvals/**`, `console/e2e/overview.spec.ts`,
`console/e2e/pages.spec.ts` (nav assertions only). **Do not edit** `usage/`, `billing/`, `pools/ai/**` — another builder
owns them. No edits outside `console/`. No `.env` files.

## Checks (PC memory is low: one heavy job at a time)
`pnpm check` must pass. Then `pnpm exec playwright test --project=behaviour overview` and `--project=screens pages`.
Update screenshot baselines only for pages you changed. Do not commit.

## Report (≤ 15 lines)
Files changed, what each page shows and from which loader, anything left out because the data does not exist, check results.
