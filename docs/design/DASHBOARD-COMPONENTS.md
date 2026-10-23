# Dashboard components — built from the owner's visual reference (for phase A3)

Owner decision 2026-10-24: the dashboard follows the look of `docs/design/reference-dashboard.png` (a social-media analytics
dashboard). We take its **layout, component patterns, spacing and colour logic**; we do **not** copy its brand name, logo,
illustration or content. This file turns the reference into Harness Farm components, so A3 builds them once and every page
composes them. Product meaning of each page: `docs/PRODUCT.md` (v2). Where the two differ on visuals, this file wins.

---

## 1. What the reference does well (and we keep)

- **Light, calm canvas:** a light grey page, white cards with a hairline border and generous radius; no heavy shadows.
- **One blue accent** for the active nav item, the primary button, the selected day / tab and the main chart; everything else
  is neutral.
- **Colour means meaning:** green / red pills only for change (up / down); category colours only inside breakdown bars.
- **Numbers first:** each card leads with one big number, then its change, then its comparison ("vs last 07 days").
- **Logos as anchors:** each KPI card carries the source's logo top-right — for us, the AI or tool logo (Claude, Codex,
  Gemini, DeepSeek, Clay…).
- **Progressive detail:** cards → dropdown filters → tabs → drill-down arrow.
- **Locale-aware numbers:** the reference groups digits the Indian way (3,50,834). We format every number with the person's
  locale from their profile (`Intl.NumberFormat`), so an Indian user sees lakhs and a US user sees thousands.

## 2. Design tokens (approximate, taken from the reference; finalised in the Round 0 mockups)

| Token | Value (approx.) | Use |
|---|---|---|
| `bg.canvas` | #F4F5F7 | Page background |
| `bg.card` | #FFFFFF | Cards, sidebar |
| `bg.subtle` | #F3F4F6 | KPI tiles, inactive chips |
| `border` | #E6E8EC, 1 px | Card and input borders |
| `accent` | #1F6FEB-like blue | Active nav, primary button, selected tab/day, main series |
| `accent.soft` | accent at ~10% | Active nav background, selected chip, highlighted card |
| `text.primary` / `text.secondary` | #111827 / #6B7280 | Titles, numbers / labels, captions |
| `positive` / `negative` | green / red + 10% tints | Delta pills and change text |
| `category.1…6` | blue, sky, purple, orange, green, amber | Breakdown bars and chart series (fixed order, colour-blind-checked) |
| Radius | 12 px cards, 8 px controls, full for pills | — |
| Spacing | 8-pt grid; 16–24 px card padding; 16 px gaps | — |
| Type | Geist Sans (UI), Geist Mono (ids, code); tabular figures | KPI 24–28 px semibold; card titles 15–16 px semibold; labels 12–13 px |
| Dark theme | Same structure, mapped tokens | Built in A3 alongside light |

## 3. Component inventory

Every component: typed props, loading skeleton, empty state, error state, keyboard and screen-reader support, light and dark.

| # | Reference element | Component | Key props (sketch) | Used on |
|---|---|---|---|---|
| 1 | Left sidebar: logo, section labels (MAIN / OTHERS), icon items, active pill | `AppSidebar`, `NavItem`, `NavSection` | `items[]`, `activeHref`, `badge?` (e.g. Approvals count), `collapsed` | Every page |
| 2 | "Unlock Premium" card at the sidebar bottom | `PlanCard` | `plan`, `capsUsed[]` (tasks/day, parallel tasks, storage), `cta?` | Sidebar (beta caps now, upgrade later) |
| 3 | Header: name + subtitle, search, bell, period dropdown, primary button, avatar | `PageHeader`, `CommandSearchButton` (⌘K), `NotificationsBell`, `PeriodSelect`, `UserMenu` | `title`, `subtitle`, `actions[]`; `PeriodSelect` is global and kept in the URL | Every page |
| 4 | KPI card: label, big number, delta pill, logo, footer comparison + arrow | `MetricCard` | `label`, `value`, `format` (number/usd/percent/tokens/duration), `delta?: {value, goodDirection: "up" \| "down"}`, `icon?` (AI/tool logo), `comparison?`, `href?` | Overview, Farm resource pages, Usage & Cost |
| 5 | Green / red change pill | `DeltaPill` | `value`, `goodDirection`, `neutralBelow?` — colour comes from sign × direction, so **spend going down is green**; `aria-label` says "down 9.1%, better" | Inside `MetricCard`, tables, insights |
| 6 | Revenue card: value + delta, source dropdown, area chart with gradient, tooltip | `TrendChartCard`, `ChartTooltip` | `title`, `value`, `delta`, `series[]` (per AI / model / tool), `metric` (tokens / cost / calls), `filters[]`; tooltip shows date, value and the split | Overview (usage over time), Usage & Cost, Farm resource pages |
| 7 | Followers card: big number, dropdown, segmented tabs, ranked list with coloured bars | `BreakdownCard`, `SegmentedTabs`, `RankedBarList` | `tabs[]` each with `rows[] {label, icon?, value, share, color}`, `maxRows`, `viewAllHref` | Overview (AI mix: By AI / By role / By tool), Usage & Cost, Farm (accounts by state) |
| 8 | Thin progress bar per row | `MeterBar` | `value`, `max`, `mode`: `category` (fixed colour) or `threshold` (neutral < 80%, amber 80–100%, red at 100%), `caption?` ("resets in 3h") | Subscription windows, budgets, plan caps, breakdowns |
| 9 | Post Schedule: week strip, day chips, time groups, item cards | `WeekStrip`, `DayChip`, `TimeGroup`, `TaskCard` → composed as `TaskBoard` | `days[]`, `selectedDay`; `TaskCard {title, operator, agents[] (avatars), status, cost, files[] (thumbnails, "+5" overflow), menu}` | Tasks (this week, by time), Overview (today's tasks) |
| 10 | Status chip "Published ✓" / "Scheduled •" | `StatusChip` | `status`: ready, running, done, waiting-for-you, failed, limited, needs-login — **icon + text, never colour alone** | Tasks, Farm, Activity, Approvals |
| 11 | Post Insights: header with meta, metric rows with average and change, split columns with coloured bars | `InsightsPanel`, `MetricRow`, `SplitBar` | header `{title, meta, icon}`; `rows[] {label, value, average, change}`; `split[] {label, share, delta, color}` | Task detail (tokens in / out, cost, checks vs average) and the **AI split** ("Claude 18% · Codex 6% · DeepSeek 76%" — the per-AI share feature) |
| 12 | Dropdowns with icons ("Last 07 Days", "YouTube", "Jan 2025") | `FilterSelect` | `options[] {value, label, icon?}`, `value`, `onChange` | Card filters, page filters |
| 13 | Icon buttons, avatar, kebab menu | shadcn/ui primitives (`Button`, `Avatar`, `DropdownMenu`) | — | Everywhere |

New for Harness Farm (not in the reference, same visual language):

| Component | Purpose |
|---|---|
| `EvidencePopover` | The "why" behind every account or tool state: observed excerpt, source run, checked at, **Re-check now** (`docs/QUALITY.md` D1) |
| `ActivityTable` + `TraceDrawer` | The activity stream with filters and saved views; the trace tree of one call (pattern from Trigger.dev) |
| `PlanGraph` + `StepTimeline` | A task's plan and its steps over time |
| `ConnectAppCard` | Ready config + permission profile + Test connection for Claude Code, Codex, Cursor, Gemini CLI |
| `CatalogDialog` | "Add to farm": search, categories, how each item connects |
| `ApprovalCard` | What, why, who, cost so far, risk; Approve / Deny with keyboard |
| `OnboardingChecklist` | The first-ten-minutes steps on an empty Overview |

## 4. Page compositions (reference grid → Harness Farm)

### Overview
```
Header: "Good morning, Amaan" · "Here's what your farm did" · ⌘K · 🔔 · Last 7 days ▾ · [Export] · avatar
Row 1  MetricCard ×4: Tool calls · Tasks done · Spend (down is good) · Saved vs baseline        (logos = icons)
Row 2  TrendChartCard "Usage over time" [by AI ▾] (2/3)   │ BreakdownCard "AI mix" tabs: By AI · By role · By tool (1/3)
Row 3  TaskBoard "This week" (WeekStrip + TaskCards) (2/3) │ InsightsPanel "Farm health" — accounts ready / limited / need you,
                                                           │   SplitBar of subscription windows used
Row 4  Recent activity (ActivityTable, 10 rows, live) (2/3)│ Recently learned (memory) (1/3)
Banner above Row 1 only when something needs the person (approvals, a login, a budget at 80%).
```

### Farm → AI → Claude (a resource page)
```
Header: Claude · "4 accounts · strategy: failover" · [Add account]
Row 1  MetricCard per account (claude-01…04): StatusChip + EvidencePopover, MeterBar of the window used, calls today, delta
Row 2  TrendChartCard "Tokens by account" (2/3)            │ BreakdownCard tabs: By model · By role · By project (1/3)
Row 3  Accounts table (label, auth type, state + why, window + resets in, success rate, actions)
```

### Usage & Cost
```
Row 1  MetricCard ×4: Actual spend · Notional value · Saved vs baseline · Cache hit rate
Row 2  TrendChartCard "Spend over time" [group by ▾] (2/3)  │ BreakdownCard tabs: AI · Role · Tool · Project (1/3)
Row 3  InsightsPanel per AI: cost per successful task, waste, vs average, SplitBar of role mix (1/3) │ Budgets with MeterBar (2/3)
Row 4  Table by the chosen group → drill into Activity
```

## 5. Build rules for A3

- Components live in `console/src/components/farm/` with a Storybook-style gallery page (`/dev/components`, dev only) that
  shows every state of every component in light and dark — the visual check runs on it.
- Charts on Recharts (already in the Console); primitives from shadcn/ui (already in the Console); no new UI library without
  an ADR.
- Every component gets a vitest unit test for its logic (e.g. `DeltaPill` colour from sign × direction) and a Playwright
  screenshot test on the gallery page in both themes.
- Numbers, dates and currency are always formatted through one `format` module using the person's locale and time zone.
- Round 0 before the build: mockups of Overview, Farm → Claude and Usage & Cost in this style for the owner's yes.
