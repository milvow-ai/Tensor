# A3D — Dashboard v2, wave 2 (three builders in parallel, same worktree, disjoint files)

Worktree `D:\Harness Farm\wt-a3`, branch `a3-dashboard`. Console = Next.js in `console/`. Data via the existing loaders in
`console/src/lib/farm/*` (fixtures in dev/e2e, Supabase views in production). Other builders are editing other pages right
now: touch ONLY the files of your section. No edits outside `console/`, no `.env` files, no commits.
**Run no checks and no Playwright** (the lead runs them; PC memory is low). Just write correct, type-safe code.

## Read first (only these)
- `docs/PRODUCT.md` §2 principles, §3 visual language, §8 engineering, §10 mapping, and the § for your page.
- `docs/design/DASHBOARD-COMPONENTS.md` — the owner-approved component set.
- `console/src/components/kit/*` — the approved UI kit (`PageHeader`, `MetricCard`, `TrendChartCard`, `BreakdownCard`,
  `StatusChip`, `MeterBar`, `FilterSelect`, `DayChip`, `DeltaPill`, `EvidencePopover`, ...). **Compose from it; no new look.**
- Reference: `console/src/app/(main)/usage/` was just rebuilt in this style — copy its structure.
- The current files of your section.

Keep every `data-testid` and accessible name that `console/e2e/*.spec.ts` uses for your pages; keep all behaviour
(forms, server actions, commands). Never show a number the Farm does not record. Period comes from `?period=` (7d default).

## Section 1 — Activity (PRODUCT §5.2) · owner: claude-02
Restyle `/runs` as **Activity** (heading "Activity"): one stream with `FilterSelect` filters (kind, status, pool), `DayChip`
grouping, `StatusChip`, compact rows, and the run detail `/runs/[id]` as a trace view in kit style.
Files: `console/src/app/(main)/runs/**` only.

## Section 2 — Memory + Settings (PRODUCT §5.6, §5.8) · owner: claude-04
`/memory` and `/settings` in kit style. Settings gets tabs per PRODUCT §4 that map to pages that exist today (Workspace,
Policies & budgets → content of `/policies`, Routing → content of `/routing`); tabs with no data yet show an honest empty
state. Files: `console/src/app/(main)/memory/**`, `console/src/app/(main)/settings/**`, `console/src/app/(main)/policies/**`,
`console/src/app/(main)/routing/**`.

## Section 3 — Farm → Tools / APIs (PRODUCT §5.4) · owner: codex-01
`/pools/tools` and `/integrations` (+ `/integrations/[id]`) in kit style: per tool pool / integration a card with
`StatusChip`, `MeterBar` for calls/limits, accounts, last error only on failure.
Files: `console/src/app/(main)/pools/tools/**`, `console/src/app/(main)/integrations/**`. Do NOT edit
`pools/_components/**` (shared, owned by another builder).

## Report (≤ 12 lines)
Files changed, data source per widget, what was left out because the data does not exist.
