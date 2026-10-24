# A3C — Dashboard v2: Usage & Cost + Farm → AI (builder: claude-02 Sonnet)

Worktree `D:\Harness Farm\wt-a3`, branch `a3-dashboard`. Console = Next.js in `console/`. Data = fixtures mode in dev/e2e
(`console/src/lib/farm/fixtures/*`) and Supabase views in production, both behind `console/src/lib/farm/*` loaders.
Another builder (A3B) is rebuilding the shell and Overview at the same time in this worktree.

## Read first (only these)
- `docs/PRODUCT.md` §2, §3, §5.4 Farm, §5.5 Usage & Cost, §8, §10.
- `docs/design/DASHBOARD-COMPONENTS.md` (approved component set; owner approved the Round 0 look on 2026-10-24).
- `console/src/components/kit/*` — the approved UI kit. **Compose pages from it; no new look.** Gallery: `/dev/components`.
- Today's `console/src/app/(main)/billing/**` and `console/src/app/(main)/pools/ai/**` (L3 sign-in screens live there:
  sign-in card, add-accounts dialog — keep all their behaviour and test ids).

## Build
1. **Usage & Cost** at `/usage` (PRODUCT §5.5) from the data `billing/` uses today: spend by AI / account / model / day
   with `MetricCard`, `TrendChartCard`, `BreakdownCard`, `DeltaPill`, `PlanCard`. Period from `?period=` (7d default; the
   shell's `PeriodSelect` writes it). `/billing` redirects to `/usage`. Never show a number the Farm does not record.
2. **Farm → AI** (`/pools/ai` and `/pools/ai/[id]`) restyled to PRODUCT §5.4 with kit components: per AI a card with
   accounts, `StatusChip` state, `MeterBar` usage/limits, reset countdown, the sign-in action for accounts that need a
   login (existing `SignInCard` / `account-signin-button`), add-accounts dialog unchanged in behaviour.

## Files you own
`console/src/app/(main)/usage/**` (new), `console/src/app/(main)/billing/**`, `console/src/app/(main)/pools/ai/**`,
`console/src/app/(main)/pools/_components/**`, `console/e2e/c2-billing-runs.spec.ts`, `console/e2e/pools.spec.ts`,
`console/e2e/signin.spec.ts`, `console/e2e/add-account.spec.ts`. **Do not edit** `layout.tsx`, the sidebar, `overview/`
(owned by A3B). No edits outside `console/`. No `.env` files.

## Checks (PC memory is low: run NO Playwright; the lead runs e2e)
`pnpm check` must pass (lint, types, build). Keep every existing test id the e2e specs use; update the specs you own if a
text or route legitimately changed (`/billing` → `/usage`). Do not commit.

## Report (≤ 15 lines)
Files changed, data source of each widget, anything left out because the data does not exist, `pnpm check` result.
