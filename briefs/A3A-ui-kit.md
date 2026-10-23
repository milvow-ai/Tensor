# A3A — Dashboard UI kit (components + gallery) from the owner's visual reference

Rules for every step: touch only the files under **Owns**. Never run `git add / commit / push / reset / checkout / stash`, deploy,
delete anything outside this worktree, or send data off this machine. Never read or print any `.env` or `console/.env.local`
file. Run every command in the foreground and wait for it; never start a long command in the background and poll it.

**Goal.** Build the reusable component kit for the new Harness Farm dashboard exactly as specified in
`docs/design/DASHBOARD-COMPONENTS.md`, matching the owner's reference image `docs/design/reference-dashboard.png` — open and
study it. This kit is Round 0: the owner reviews the gallery page before any page is rebuilt. The bar is a senior
design-engineering team's work: precise spacing, consistent tokens, every state designed — not a template.

**Read first:** `docs/design/DASHBOARD-COMPONENTS.md` (all of it) · `docs/design/reference-dashboard.png` · `docs/PRODUCT.md` §2–3 ·
`console/AGENTS.md` · `console/package.json` · `console/src/components/ui/` (existing shadcn/ui primitives: reuse them) · the
root layout and global CSS under `console/src/app` (how fonts and themes are set today).

**Steps:**
1. **Tokens:** add the tokens of `DASHBOARD-COMPONENTS.md` §2 as CSS variables for light and dark (Tailwind v4 theme) in a new
   file used by the kit and the gallery only — existing pages must look exactly as before. Fonts: Geist Sans and Geist Mono
   through the `geist` package (`next/font`), scoped to the kit and gallery.
2. **Components** in `console/src/components/kit/`, one file each, typed props, no `any`, `forwardRef` where a component wraps a
   DOM element: AppSidebar (+ NavItem, NavSection), PlanCard, PageHeader, PeriodSelect, NotificationsBell,
   CommandSearchButton, MetricCard, DeltaPill, TrendChartCard (+ ChartTooltip; Recharts area chart with a soft gradient),
   BreakdownCard (+ SegmentedTabs, RankedBarList), MeterBar, WeekStrip, DayChip, TaskCard (+ TaskBoard), StatusChip,
   InsightsPanel (+ MetricRow, SplitBar), FilterSelect, EvidencePopover, ApprovalCard, ConnectAppCard, OnboardingChecklist.
   (ActivityTable / TraceDrawer and PlanGraph / StepTimeline come later — skip them.)
3. **`console/src/lib/format.ts`:** one formatter for numbers, compact numbers, currency, percent, tokens, durations, relative
   time and dates, built on `Intl` with explicit locale and time-zone arguments; `en-IN` must format 350834 as `3,50,834`.
4. **`DeltaPill`:** colour from sign × `goodDirection` (spend going down is positive / green); neutral below a threshold;
   `aria-label` such as "down 9.1%, better".
5. **Gallery page `/dev/components`** in a new route group, not linked from the nav, returning 404 when
   `NODE_ENV === 'production'`: every component in every state (default, loading skeleton, empty, error, all status variants),
   with a light / dark toggle, using realistic Harness Farm sample data — AIs Claude, Codex, Gemini, DeepSeek; tools Clay,
   Sentry, Figma; tasks like "Build the pricing section". No lorem ipsum, no social-media content, nothing from the reference's
   brand.
6. **Tests:** vitest for `format.ts` (including `en-IN` grouping) and for `DeltaPill`'s colour and `aria-label` logic; one
   Playwright test that opens the gallery and saves full-page screenshots in light and dark to
   `console/e2e/__screenshots__/kit/` (saved, not yet asserted pixel by pixel).
7. In `console/`, run `pnpm check`, `pnpm test`, `pnpm test:e2e`.

**Owns:** `console/src/components/kit/**` (new) · `console/src/lib/format.ts` (new) · the new gallery route group under
`console/src/app/` · one new token stylesheet for the kit · `console/e2e/kit.spec.ts` (new) · unit tests next to the new
files · `console/package.json` + `console/pnpm-lock.yaml` (only to add `geist`).

**Must not touch:** existing pages, existing components (`console/src/components/farm`, `console/src/components/ui` — use them,
do not edit them), `console/sql`, anything outside `console/` except reading the docs, any `.env` file.

**Done when:** `pnpm check`, `pnpm test` and `pnpm test:e2e` pass; the gallery renders every component in both themes; the
screenshots exist.

**Reply (15 lines or fewer):** components built · files · test results · screenshot paths · any deviation from the spec and why.
