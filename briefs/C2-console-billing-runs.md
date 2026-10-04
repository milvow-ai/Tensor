# Brief C2 — Console: Billing and Runs

Rules: follow `briefs/CONTEXT.md` §6 and the §0 quality bar. Touch only the files under "Owns". Never git add/commit/push, never deploy. Never read `.env`/`console/.env.local`.

## Goal
Two screens that answer the owner's daily questions. **Billing:** "what am I spending, where, will I stay inside budget, what renews soon, which paid
accounts are idle?" **Runs:** "what did the Farm just do for Claude, which account answered, what fell back and why, what did it cost, show me the
evidence." Built on the C1 shell, data layer, design system and both data sources (supabase + fixtures).

## Read first
`briefs/CONTEXT.md` (§3 tables: runs, run_events, usage_events, billing_events, budgets, alerts, evidence; §4 envelope), `HANDOFF.md` §4.4, §5.1 rows 3–4,
the C1 code in `console/` (data layer, components, page patterns, e2e harness), `console/sql/views.sql`.

## Owns
`console/src/app/**/billing/**`, `console/src/app/**/runs/**`, additions to `console/src/lib/farm/*` (new interface methods + both implementations +
fixtures), shared components you add under `console/src/components/farm/`, `console/sql/views_c2.sql` (new views: `v_spend_daily` (date × provider),
`v_cost_per_result` (provider/connection: spend ÷ successful results this month), `v_renewals` (next renewal date, price, usage % of allowance),
`v_idle_paid` (paid connections with 0 successful calls in 14 days), `v_run_detail` (run + ordered events + final connection + evidence ids)),
e2e tests `console/tests/c2-*.spec.ts`, forecast unit tests `console/src/lib/farm/__tests__/forecast.test.ts` (vitest if not present — add it).

## Billing page
KPI row: spend month-to-date vs global budget (progress with 50/80/100 % markers), forecast to month end (method shown on hover: spend ÷ elapsed days ×
days in month; flag when forecast > budget), paid accounts count, idle paid accounts. Spend over time (Recharts stacked area by provider, 30/90 days).
Budgets table (scope, monthly cap, spent, forecast, hard-stop toggle → `set_budget` command; edit cap inline with validation). Renewal calendar (next 45
days, list grouped by week: provider, account, price, usage % — "consider cancelling" hint when usage < 20 %; cancellation is a human task link, never
automated). Cost per result per provider/account (sortable). Idle paid accounts with "pause" action.

## Runs page
Filterable, paginated table (capability, status incl. blocked, caller (claude/hermes/console/cli), provider/account, cached, cost, duration, time;
filters: capability, status, failures only, caller, date range; URL-synced). Row → run detail drawer/page: timeline of `run_events` (plan, candidates with
ranking reasons, skips with reasons, reserve/execute/success/failure/fallback/commit/release, cache hits, single-flight joins) as a vertical stepper with
timestamps and per-step latency; final result envelope (JSON viewer, collapsible, secrets impossible by construction — still redact anything that
looks like a key); evidence thumbnails (Supabase Storage URLs when present, else "evidence on the Farm PC" state). Realtime (or 5 s poll) updates the
list while open — Runs is one of the two Realtime pages allowed.

## Done when
`pnpm check` and `pnpm test:e2e` green (new specs: billing renders KPIs/forecast flag from fixtures; budget edit enqueues `set_budget` and validates;
runs filter "failures only" works and URL-syncs; run detail shows the fallback step with its reason); forecast unit tests cover month boundaries,
day 1, leap February, zero spend; screenshots of both pages at 1440 and 390 px in `D:\dev-cache\shots\C2\`; the C1 bundle limit still holds.

## Reply (≤ 15 lines)
Files/areas; check + e2e tails; screenshots path; bundle sizes; deviations.
