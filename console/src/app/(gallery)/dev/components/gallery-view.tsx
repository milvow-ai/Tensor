"use client";

import { useState } from "react";

import {
  Activity,
  Bot,
  Brain,
  CheckCircle2,
  Clock,
  Code2,
  Cpu,
  Globe,
  Layers,
  LayoutDashboard,
  Moon,
  Plus,
  Settings,
  ShieldCheck,
  Sparkles,
  Sun,
  Wrench,
  Zap,
} from "lucide-react";

import {
  ApprovalCard,
  AppSidebar,
  BreakdownCard,
  CommandSearchButton,
  ConnectAppCard,
  DayChip,
  DeltaPill,
  EvidencePopover,
  type FarmStatus,
  FilterSelect,
  InsightsPanel,
  MeterBar,
  MetricCard,
  NotificationsBell,
  OnboardingChecklist,
  PageHeader,
  PeriodSelect,
  PlanCard,
  StatusChip,
  TaskBoard,
  TaskCard,
  TrendChartCard,
  WeekStrip,
} from "@/components/kit";

export function GalleryView() {
  const [isDark, setIsDark] = useState(false);
  const [locale, setLocale] = useState<"en-US" | "en-IN">("en-US");
  const [selectedPeriod, setSelectedPeriod] = useState("7d");
  const [selectedAiFilter, setSelectedAiFilter] = useState("all");
  const [selectedDay, setSelectedDay] = useState("day-3");
  const [approvalFeedback, setApprovalFeedback] = useState<string | null>(null);

  const toggleTheme = () => {
    const nextDark = !isDark;
    setIsDark(nextDark);
    if (nextDark) {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  };

  const trendData = [
    { date: "Oct 18", claude: 180, deepseek: 120, codex: 85, gemini: 45 },
    { date: "Oct 19", claude: 240, deepseek: 160, codex: 110, gemini: 60 },
    { date: "Oct 20", claude: 290, deepseek: 210, codex: 130, gemini: 75 },
    { date: "Oct 21", claude: 380, deepseek: 290, codex: 180, gemini: 90 },
    { date: "Oct 22", claude: 420, deepseek: 340, codex: 210, gemini: 110 },
    { date: "Oct 23", claude: 490, deepseek: 410, codex: 240, gemini: 130 },
    { date: "Oct 24", claude: 560, deepseek: 480, codex: 290, gemini: 155 },
  ];

  const trendSeries = [
    { name: "Claude", key: "claude", color: "var(--kit-category-1, #2563eb)" },
    { name: "DeepSeek", key: "deepseek", color: "var(--kit-category-2, #0284c7)" },
    { name: "Codex", key: "codex", color: "var(--kit-category-3, #9333ea)" },
    { name: "Gemini", key: "gemini", color: "var(--kit-category-4, #ea580c)" },
  ];

  const sidebarSections = [
    {
      id: "main",
      label: "MAIN",
      items: [
        { title: "Overview", href: "/dev/components#overview", icon: LayoutDashboard },
        { title: "Activity", href: "/dev/components#activity", icon: Activity },
        { title: "Tasks", href: "/dev/components#tasks", icon: CheckCircle2 },
        { title: "Farm", href: "/dev/components#farm", icon: Cpu },
        { title: "Usage & Cost", href: "/dev/components#usage", icon: Zap },
        { title: "Approvals", href: "/dev/components#approvals", icon: ShieldCheck, badge: 3 },
      ],
    },
    {
      id: "others",
      label: "OTHERS",
      items: [
        { title: "Settings", href: "/dev/components#settings", icon: Settings },
        { title: "Docs", href: "/dev/components#docs", icon: Globe },
      ],
    },
  ];

  const weekDays = [
    { id: "day-1", label: "Mon", dayNumber: 19, hasItems: true },
    { id: "day-2", label: "Tue", dayNumber: 20, hasItems: true },
    { id: "day-3", label: "Wed", dayNumber: 21, hasItems: true, badgeCount: 4 },
    { id: "day-4", label: "Thu", dayNumber: 22, hasItems: false },
    { id: "day-5", label: "Fri", dayNumber: 23, hasItems: true },
    { id: "day-6", label: "Sat", dayNumber: 24, hasItems: false },
    { id: "day-7", label: "Sun", dayNumber: 25, hasItems: false },
  ];

  const taskGroups = [
    {
      id: "morning",
      title: "Morning (09:00 - 12:00)",
      tasks: [
        {
          title: "Build the pricing section",
          operator: "Amaan",
          agents: [
            { name: "Claude", role: "Coder" },
            { name: "Codex", role: "Reviewer" },
          ],
          status: "running" as FarmStatus,
          cost: 1.45,
          time: "10:15 AM",
          files: [{ name: "pricing.tsx" }, { name: "tiers.json" }],
        },
        {
          title: "Sync Clay MCP enrichment contacts",
          operator: "Amaan",
          agents: [{ name: "DeepSeek", role: "Worker" }],
          status: "done" as FarmStatus,
          cost: 0.35,
          time: "11:45 AM",
          files: [{ name: "leads.csv" }],
        },
      ],
    },
    {
      id: "afternoon",
      title: "Afternoon (12:00 - 17:00)",
      tasks: [
        {
          title: "Review PR: Sentry error handling",
          operator: "Amaan",
          agents: [{ name: "Gemini", role: "Auditor" }],
          status: "waiting-for-you" as FarmStatus,
          cost: 0.8,
          time: "02:30 PM",
          files: [{ name: "sentry.ts" }, { name: "logger.ts" }, { name: "report.md" }, { name: "extra.json" }],
        },
        {
          title: "Re-index Farm vector memories",
          operator: "Amaan",
          agents: [{ name: "Codex", role: "Indexer" }],
          status: "ready" as FarmStatus,
          cost: 0.12,
          time: "04:00 PM",
        },
      ],
    },
    {
      id: "evening",
      title: "Evening (17:00 - 21:00)",
      tasks: [
        {
          title: "Automated schema migration run",
          operator: "Amaan",
          agents: [{ name: "Codex", role: "DBA" }],
          status: "failed" as FarmStatus,
          cost: 0.95,
          time: "06:15 PM",
          files: [{ name: "migration.sql" }],
        },
        {
          title: "Anthropic Claude API pool health check",
          operator: "Amaan",
          agents: [{ name: "Claude", role: "HealthCheck" }],
          status: "needs-login" as FarmStatus,
          cost: 0.0,
          time: "07:30 PM",
        },
      ],
    },
  ];

  const onboardingSteps = [
    {
      id: "step-1",
      title: "Connect an AI provider",
      description: "API keys for Claude, Codex, Gemini or DeepSeek configured in Farm pool.",
      completed: true,
    },
    {
      id: "step-2",
      title: "Connect a tool integration",
      description: "MCP servers connected for Clay, Sentry, or Figma.",
      completed: true,
    },
    {
      id: "step-3",
      title: "Connect your AI app (Claude Code / Codex CLI)",
      description: "Export the connection snippet and permission profile into your terminal.",
      completed: false,
      current: true,
      action: {
        label: "Connect app",
        onClick: () => alert("Opened Connect App modal"),
      },
    },
    {
      id: "step-4",
      title: "Run your first delegated task",
      description: "Trigger an autonomous coding task through your nerve system.",
      completed: false,
      action: {
        label: "Start task",
        onClick: () => alert("Task kickoff initiated"),
      },
    },
  ];

  return (
    <div data-kit-theme={isDark ? "dark" : "light"} className="mx-auto max-w-[1440px] space-y-12 p-4 sm:p-6 lg:p-8">
      {/* Gallery Header & Controls */}
      <div className="flex flex-col justify-between gap-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 shadow-[var(--kit-shadow-card)] sm:flex-row sm:items-center dark:border-[#30363d] dark:bg-[#161b22]">
        <div>
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-[var(--kit-accent,#1a63d6)]" />
            <h1 className="font-bold text-[var(--kit-text-primary,#111827)] text-xl tracking-tight dark:text-[#f0f6fc]">
              Component gallery
            </h1>
          </div>
          <p className="mt-1 text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Every Harness Farm component in every state, light and dark.
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {/* Locale switcher */}
          <div className="flex items-center gap-1.5 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-1 text-xs dark:bg-[#21262d]">
            <span className="px-1.5 font-medium text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
              Locale:
            </span>
            <button
              type="button"
              data-testid="locale-en-us"
              onClick={() => setLocale("en-US")}
              className={`cursor-pointer rounded px-2 py-0.5 font-semibold text-xs ${
                locale === "en-US"
                  ? "bg-[var(--kit-bg-card,#ffffff)] text-[var(--kit-accent,#1a63d6)] shadow-sm dark:bg-[#161b22]"
                  : "text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]"
              }`}
            >
              en-US
            </button>
            <button
              type="button"
              data-testid="locale-en-in"
              onClick={() => setLocale("en-IN")}
              className={`cursor-pointer rounded px-2 py-0.5 font-semibold text-xs ${
                locale === "en-IN"
                  ? "bg-[var(--kit-bg-card,#ffffff)] text-[var(--kit-accent,#1a63d6)] shadow-sm dark:bg-[#161b22]"
                  : "text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]"
              }`}
            >
              en-IN
            </button>
          </div>

          {/* Theme switcher */}
          <button
            type="button"
            data-testid="theme-toggle"
            onClick={toggleTheme}
            className="flex cursor-pointer items-center gap-2 rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] px-3 py-1.5 font-medium text-[var(--kit-text-primary,#111827)] text-xs shadow-sm transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#f0f6fc] dark:hover:bg-[#21262d]"
          >
            {isDark ? (
              <>
                <Sun className="h-3.5 w-3.5 text-amber-400" />
                <span>Light mode</span>
              </>
            ) : (
              <>
                <Moon className="h-3.5 w-3.5 text-blue-600" />
                <span>Dark mode</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* SECTION 1: StatusChip (All 7 variants + sizes + pulsing) */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            1. StatusChip
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Icon and text status indicators across all operational states in standard, small, and pulsing modes.
          </p>
        </div>

        <div className="space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22]">
          <div className="space-y-2">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              Standard size (md)
            </span>
            <div className="flex flex-wrap items-center gap-2.5">
              <StatusChip status="ready" />
              <StatusChip status="running" />
              <StatusChip status="done" />
              <StatusChip status="waiting-for-you" />
              <StatusChip status="failed" />
              <StatusChip status="limited" />
              <StatusChip status="needs-login" />
            </div>
          </div>

          <div className="space-y-2 border-[var(--kit-border,#e6e8ec)] border-t pt-2 dark:border-[#30363d]">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              Small size (sm) & Pulsing live state
            </span>
            <div className="flex flex-wrap items-center gap-2.5">
              <StatusChip status="ready" size="sm" />
              <StatusChip status="running" size="sm" pulsing />
              <StatusChip status="done" size="sm" />
              <StatusChip status="waiting-for-you" size="sm" />
              <StatusChip status="failed" size="sm" />
              <StatusChip status="limited" size="sm" />
              <StatusChip status="needs-login" size="sm" />
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 2: DeltaPill */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            2. DeltaPill
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Direction-aware change pills where spend decreases are highlighted positively, with accessible screen-reader labels.
          </p>
        </div>

        <div className="space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22]">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-2 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-3.5 dark:bg-[#0d1117]">
              <span className="block font-semibold text-[var(--kit-text-primary,#111827)] text-xs dark:text-[#f0f6fc]">
                goodDirection="up" (e.g. Tasks Done, Tool Calls)
              </span>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <DeltaPill value={12.4} goodDirection="up" />
                <DeltaPill value={-8.2} goodDirection="up" />
                <DeltaPill value={0} goodDirection="up" />
                <DeltaPill value={0.03} goodDirection="up" neutralBelow={0.05} />
              </div>
            </div>

            <div className="space-y-2 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-3.5 dark:bg-[#0d1117]">
              <span className="block font-semibold text-[var(--kit-text-primary,#111827)] text-xs dark:text-[#f0f6fc]">
                goodDirection="down" (e.g. Spend, Error Rate, Latency)
              </span>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <DeltaPill value={-9.1} goodDirection="down" />
                <DeltaPill value={14.2} goodDirection="down" />
                <DeltaPill value={0} goodDirection="down" />
                <DeltaPill value={-0.02} goodDirection="down" neutralBelow={0.05} />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 3: MetricCard (Default, en-IN lakhs, skeleton, empty, error, link) */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            3. MetricCard
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Primary metric tiles with change indicators, comparisons, and locale-aware digit grouping.
          </p>
        </div>

        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Tool calls"
              value={1284}
              format="number"
              locale={locale}
              delta={{ value: 12, goodDirection: "up" }}
              comparison="vs last 7 days"
              icon={<Wrench className="h-5 w-5 text-blue-500" />}
            />
            <MetricCard
              label="Tasks done"
              value={37}
              format="number"
              locale={locale}
              delta={{ value: 4, goodDirection: "up" }}
              comparison="vs last 7 days"
              icon={<CheckCircle2 className="h-5 w-5 text-emerald-500" />}
            />
            <MetricCard
              label="Spend (down is good)"
              value={18.4}
              format="usd"
              locale={locale}
              delta={{ value: -9.1, goodDirection: "down" }}
              comparison="vs last 7 days"
              icon={<Zap className="h-5 w-5 text-amber-500" />}
            />
            <MetricCard
              label="Saved vs baseline"
              value={61.2}
              format="usd"
              locale={locale}
              delta={{ value: 18.5, goodDirection: "up" }}
              comparison="vs last 7 days"
              icon={<Sparkles className="h-5 w-5 text-purple-500" />}
            />
          </div>

          {/* Special Locale demonstration: 350834 as 3,50,834 in en-IN */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Tokens consumed"
              value={350834}
              format="number"
              locale={locale}
              delta={{ value: 5.2, goodDirection: "down" }}
              comparison="vs last 7 days"
              icon={<Cpu className="h-5 w-5 text-indigo-500" />}
              href="#drilldown-tokens"
            />
            {/* Loading skeleton state */}
            <MetricCard label="Loading metric" value={0} loading />
            {/* Empty state */}
            <MetricCard label="Unused pool calls" value={0} empty comparison="No records yet" />
            {/* Error state */}
            <MetricCard label="API Quota" value={0} error="Connection timeout to provider" />
          </div>
        </div>
      </section>

      {/* SECTION 4: MeterBar */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            4. MeterBar
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Threshold mode (&lt;80% neutral, 80–99% amber, 100% red) and category fixed color mode.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-6 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 md:grid-cols-2 dark:border-[#30363d] dark:bg-[#161b22]">
          <div className="space-y-4">
            <h3 className="font-bold text-[var(--kit-text-muted,#9ca3af)] text-xs uppercase tracking-wider dark:text-[#6e7681]">
              Threshold mode
            </h3>
            <MeterBar
              label="Normal usage (< 80%)"
              value={42}
              max={100}
              mode="threshold"
              caption="Healthy window"
              showValue
            />
            <MeterBar
              label="Warning window (80% - 99%)"
              value={85}
              max={100}
              mode="threshold"
              caption="Rate limit threshold approaching"
              showValue
            />
            <MeterBar
              label="Exhausted window (100%)"
              value={100}
              max={100}
              mode="threshold"
              caption="Exhausted · resets in 3h"
              showValue
            />
          </div>

          <div className="space-y-4">
            <h3 className="font-bold text-[var(--kit-text-muted,#9ca3af)] text-xs uppercase tracking-wider dark:text-[#6e7681]">
              Category mode & States
            </h3>
            <MeterBar
              label="Claude Token Allowance"
              value={65}
              max={100}
              mode="category"
              color="var(--kit-category-1, #2563eb)"
              caption="65% allocated"
              showValue
            />
            <MeterBar
              label="DeepSeek Token Allowance"
              value={88}
              max={100}
              mode="category"
              color="var(--kit-category-2, #0284c7)"
              caption="88% allocated"
              showValue
            />
            <MeterBar label="Loading meter bar" value={0} max={100} loading />
            <MeterBar label="Errored meter" value={0} max={100} error="Quota sync failed" />
          </div>
        </div>
      </section>

      {/* SECTION 5: TrendChartCard & ChartTooltip */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            5. TrendChartCard & ChartTooltip
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Recharts stacked area chart with soft gradient, model filters, and hover tooltip with percentage splits.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <TrendChartCard
              title="Usage over time"
              value={1485}
              delta={{ value: 16.2, goodDirection: "up" }}
              series={trendSeries}
              data={trendData}
              metric="calls"
              filters={[
                { value: "all", label: "All AIs" },
                { value: "claude", label: "Claude only" },
                { value: "deepseek", label: "DeepSeek only" },
              ]}
              selectedFilter={selectedAiFilter}
              onFilterChange={setSelectedAiFilter}
            />
          </div>

          <div className="space-y-4">
            <TrendChartCard title="Loading skeleton state" series={trendSeries} data={[]} loading />
            <TrendChartCard title="Empty state (no events)" series={trendSeries} data={[]} empty />
          </div>
        </div>
      </section>

      {/* SECTION 6: BreakdownCard, SegmentedTabs, RankedBarList */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            6. BreakdownCard (+ SegmentedTabs & RankedBarList)
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Interactive tabs (By AI / By role / By tool) with ranked bar list, category colors, and view-all link.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <BreakdownCard
            title="AI mix"
            totalValue="1.4M tokens"
            tabs={[
              {
                id: "ai",
                label: "By AI",
                rows: [
                  {
                    label: "Claude Sonnet 5.5",
                    value: "588K",
                    share: 42,
                    icon: <Bot className="h-3.5 w-3.5 text-blue-500" />,
                  },
                  {
                    label: "DeepSeek V4",
                    value: "434K",
                    share: 31,
                    icon: <Brain className="h-3.5 w-3.5 text-sky-500" />,
                  },
                  {
                    label: "Codex",
                    value: "252K",
                    share: 18,
                    icon: <Code2 className="h-3.5 w-3.5 text-purple-500" />,
                  },
                  {
                    label: "Gemini 3.8 Flash",
                    value: "126K",
                    share: 9,
                    icon: <Zap className="h-3.5 w-3.5 text-orange-500" />,
                  },
                ],
              },
              {
                id: "role",
                label: "By role",
                rows: [
                  { label: "Coder", value: "532K", share: 38 },
                  { label: "Planner", value: "490K", share: 35 },
                  { label: "Reviewer", value: "238K", share: 17 },
                  { label: "Tester", value: "140K", share: 10 },
                ],
              },
              {
                id: "tool",
                label: "By tool",
                rows: [
                  { label: "Clay MCP", value: "630K", share: 45 },
                  { label: "Sentry", value: "420K", share: 30 },
                  { label: "Figma API", value: "350K", share: 25 },
                ],
              },
            ]}
            viewAllHref="#view-all-breakdown"
          />

          <BreakdownCard title="Loading skeleton" tabs={[{ id: "ai", label: "By AI", rows: [] }]} loading />

          <BreakdownCard
            title="Error state"
            tabs={[{ id: "ai", label: "By AI", rows: [] }]}
            error="Failed to load rollup table data"
          />
        </div>
      </section>

      {/* SECTION 7: WeekStrip & DayChip */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            7. WeekStrip & DayChip
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Schedule day selection, dot indicators, and task count badges.
          </p>
        </div>

        <div className="space-y-4">
          <WeekStrip
            title="Task Schedule (This Week)"
            days={weekDays}
            selectedDayId={selectedDay}
            onSelectDay={setSelectedDay}
            action={
              <button
                type="button"
                className="inline-flex items-center gap-1 font-medium text-[var(--kit-accent,#1a63d6)] text-xs hover:underline dark:text-[#58a6ff]"
              >
                <Plus className="h-3.5 w-3.5" />
                <span>New task</span>
              </button>
            }
          />

          <div className="space-y-2 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-4 dark:border-[#30363d] dark:bg-[#161b22]">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              DayChip Standalone States
            </span>
            <div className="flex flex-wrap items-center gap-3 pt-1">
              <DayChip label="Default" dayNumber={12} />
              <DayChip label="Selected" dayNumber={13} isSelected />
              <DayChip label="Has items" dayNumber={14} hasItems />
              <DayChip label="Badge" dayNumber={15} badgeCount={5} />
              <DayChip label="Disabled" dayNumber={16} disabled />
              <DayChip label="Loading" dayNumber={0} loading />
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 8: TaskCard & TaskBoard */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            8. TaskCard & TaskBoard
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Schedule cards with operator, agent avatars, status chip, cost, file thumbnails, and menu.
          </p>
        </div>

        <div className="space-y-4">
          <TaskBoard groups={taskGroups} />

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
                Loading skeleton
              </span>
              <TaskCard title="..." operator="..." status="ready" loading />
            </div>
            <div className="space-y-2">
              <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
                Single card: Waiting for you
              </span>
              <TaskCard
                title="Approve database credential rotation"
                operator="Amaan"
                agents={[{ name: "Claude" }]}
                status="waiting-for-you"
                cost={0.25}
                time="Just now"
              />
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 9: InsightsPanel, MetricRow, SplitBar */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            9. InsightsPanel (+ MetricRow & SplitBar)
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Performance metrics with benchmark averages and AI distribution.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <InsightsPanel
            header={{
              title: "Farm Health & Efficiency",
              meta: "Benchmark vs 30-day average",
              icon: <Zap className="h-4 w-4" />,
            }}
            rows={[
              {
                label: "Tokens consumed",
                value: "1.2M",
                average: "980K",
                change: { value: 14.5, goodDirection: "up" },
              },
              {
                label: "Latency p95",
                value: "1.4s",
                average: "2.1s",
                change: { value: -33.3, goodDirection: "down" }, // down is green/better!
              },
              {
                label: "Cost per task",
                value: "$0.18",
                average: "$0.22",
                change: { value: -18.2, goodDirection: "down" }, // down is green/better!
              },
            ]}
            splitTitle="AI Share Split"
            split={[
              { label: "Claude", share: 18, color: "var(--kit-category-1, #2563eb)" },
              { label: "Codex", share: 6, color: "var(--kit-category-3, #9333ea)" },
              { label: "DeepSeek", share: 76, color: "var(--kit-category-2, #0284c7)" },
            ]}
          />

          <div className="space-y-4">
            <InsightsPanel header={{ title: "Loading skeleton", icon: <Clock className="h-4 w-4" /> }} loading />
            <InsightsPanel
              header={{ title: "Error state", icon: <Bot className="h-4 w-4" /> }}
              error="Telemetry service unreachable"
            />
          </div>
        </div>
      </section>

      {/* SECTION 10: AppSidebar & PlanCard */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            10. AppSidebar & PlanCard
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Left sidebar with section headers (MAIN / OTHERS), active nav item, approvals count badge, and PlanCard
            bottom tile.
          </p>
        </div>

        <div className="grid h-[720px] grid-cols-1 gap-6 md:grid-cols-2">
          <div className="overflow-hidden rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] dark:border-[#30363d]">
            <AppSidebar
              brandName="Harness Farm"
              sections={sidebarSections}
              activeHref="/dev/components#overview"
              footer={
                <PlanCard
                  plan="Beta Pro Plan"
                  capsUsed={[
                    { label: "Tasks / day", used: 37, total: 50 },
                    { label: "Parallel tasks", used: 3, total: 5 },
                    { label: "Storage", used: 14.2, total: 20, unit: "GB" },
                  ]}
                  cta={{
                    label: "Manage beta limits",
                    onClick: () => alert("Opened beta limits modal"),
                  }}
                />
              }
            />
          </div>

          <div className="space-y-4">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              PlanCard standalone & states
            </span>
            <PlanCard
              plan="Beta Starter"
              capsUsed={[
                { label: "Tasks / day", used: 48, total: 50 }, // 96% -> amber warning
                { label: "Parallel tasks", used: 5, total: 5 }, // 100% -> red limit
              ]}
              cta={{ label: "Request limit raise" }}
            />
            <PlanCard plan="Loading" capsUsed={[]} loading />
          </div>
        </div>
      </section>

      {/* SECTION 11: PageHeader, PeriodSelect, NotificationsBell, CommandSearchButton */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            11. PageHeader (+ PeriodSelect, NotificationsBell, CommandSearchButton)
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Global header composition with search trigger (⌘K), notifications bell, global URL period selector, and
            action buttons.
          </p>
        </div>

        <div className="space-y-4">
          <div className="overflow-hidden rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] dark:border-[#30363d]">
            <PageHeader
              title="Good morning, Amaan"
              subtitle="Here's what your farm did"
              search={<CommandSearchButton onClick={() => alert("⌘K triggered")} />}
              periodSelect={<PeriodSelect value={selectedPeriod} onChange={setSelectedPeriod} />}
              bell={<NotificationsBell count={3} onClick={() => alert("Notifications clicked")} />}
              actions={[
                <button
                  key="export"
                  type="button"
                  className="cursor-pointer rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] px-3 py-1.5 font-medium text-[var(--kit-text-primary,#111827)] text-xs hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#f0f6fc]"
                >
                  Export
                </button>,
                <button
                  key="add"
                  type="button"
                  className="cursor-pointer rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-accent,#1a63d6)] px-3 py-1.5 font-medium text-white text-xs shadow-sm hover:bg-[var(--kit-accent-hover,#195ac0)]"
                >
                  Add to farm
                </button>,
              ]}
              userMenu={
                <div className="flex cursor-pointer items-center gap-2 pl-2">
                  <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--kit-accent,#1a63d6)] font-bold text-white text-xs">
                    A
                  </div>
                </div>
              }
            />
          </div>

          <div className="space-y-2 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-4 dark:border-[#30363d] dark:bg-[#161b22]">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              PageHeader Loading Skeleton
            </span>
            <PageHeader title="..." loading />
          </div>
        </div>
      </section>

      {/* SECTION 12: FilterSelect, EvidencePopover, ApprovalCard */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            12. FilterSelect, EvidencePopover, ApprovalCard
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Evidence-backed states ("The Why ▸"), keyboard-ready Approvals (A / D), and icon filters.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
          {/* Evidence Popover */}
          <div className="space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22]">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              EvidencePopover (Click to open)
            </span>
            <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
              Account{" "}
              <span className="font-mono text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                claude-02
              </span>{" "}
              is in <StatusChip status="needs-login" size="sm" /> state:
            </p>
            <div className="pt-2">
              <EvidencePopover
                state="Needs login"
                observedExcerpt="OAuth refresh token expired (invalid_grant: token has been revoked by provider) during call to Anthropic Messages API."
                sourceRun={{ id: "run-4821", label: "Run #run-4821" }}
                checkedAt={new Date(Date.now() - 1000 * 60 * 14)}
                onRecheck={async () => {
                  await new Promise((r) => setTimeout(r, 600));
                  alert("Re-checked account: token still expired. Please run `farm ai login`.");
                }}
              />
            </div>
          </div>

          {/* ApprovalCard */}
          <div className="space-y-2">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              ApprovalCard
            </span>
            <ApprovalCard
              what={{
                tool: "Clay MCP",
                action: "write_enrichment",
                target: "api.clay.run/v1/enrich",
              }}
              why="Need to populate company revenue tier and employee headcount for 40 prospective accounts."
              who={{
                operator: "Amaan",
                agent: "Claude Code",
              }}
              costSoFar={1.45}
              risk="high"
              onApprove={() => setApprovalFeedback("Approved Clay MCP write action")}
              onDeny={() => setApprovalFeedback("Denied Clay MCP write action")}
            />
            {approvalFeedback && (
              <div className="rounded bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] p-2 font-semibold text-[var(--kit-accent,#1a63d6)] text-xs dark:text-[#58a6ff]">
                Action: {approvalFeedback}
              </div>
            )}
          </div>

          {/* FilterSelect */}
          <div className="space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22]">
            <span className="font-bold text-[11px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
              FilterSelect with Icons
            </span>
            <div className="space-y-3">
              <FilterSelect
                value="claude"
                onChange={(val) => {
                  void val;
                }}
                options={[
                  { value: "claude", label: "Claude Sonnet 5.5", icon: <Bot className="h-3.5 w-3.5 text-blue-500" /> },
                  { value: "deepseek", label: "DeepSeek V4", icon: <Brain className="h-3.5 w-3.5 text-sky-500" /> },
                  { value: "codex", label: "Codex", icon: <Code2 className="h-3.5 w-3.5 text-purple-500" /> },
                  { value: "gemini", label: "Gemini 3.8 Flash", icon: <Zap className="h-3.5 w-3.5 text-orange-500" /> },
                ]}
              />

              <FilterSelect
                value="clay"
                onChange={(val) => {
                  void val;
                }}
                options={[
                  { value: "clay", label: "Clay MCP", icon: <Wrench className="h-3.5 w-3.5 text-emerald-500" /> },
                  { value: "sentry", label: "Sentry API", icon: <Activity className="h-3.5 w-3.5 text-purple-500" /> },
                  { value: "figma", label: "Figma Tool", icon: <Layers className="h-3.5 w-3.5 text-pink-500" /> },
                ]}
              />

              <FilterSelect
                value=""
                onChange={(val) => {
                  void val;
                }}
                options={[]}
                loading
              />
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 13: ConnectAppCard & OnboardingChecklist */}
      <section className="space-y-4">
        <div className="border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <h2 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">
            13. ConnectAppCard & OnboardingChecklist
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Ready configs + permission profiles + test connection; first-ten-minutes guided setup.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="space-y-4">
            <ConnectAppCard
              app={{
                name: "Claude Code",
                id: "claude-code",
                icon: <Bot className="h-4 w-4 text-blue-500" />,
                description: "Terminal agent with local bash & MCP tools execution profile.",
              }}
              configSnippet={`farm connect claude-code
# or by hand (the token stays in your environment):
claude mcp add --transport http harness-farm http://127.0.0.1:8787/mcp \\
  --header "Authorization: Bearer \${FARM_TOKEN}"`}
              permissionProfile="allow:read,allow:write,ask:approval_above_1usd"
              onTestConnection={async () => {
                await new Promise((r) => setTimeout(r, 700));
                return true;
              }}
            />

            <ConnectAppCard
              app={{
                name: "Cursor IDE",
                id: "cursor",
                icon: <Code2 className="h-4 w-4 text-purple-500" />,
                description: "Codebase editing and background indexing with shared memory.",
              }}
              configSnippet={`{
  "mcpServers": {
    "harness-farm": {
      "command": "farm",
      "args": ["serve"]
    }
  }
}`}
              permissionProfile="allow:all_local_tools"
              onTestConnection={async () => {
                await new Promise((r) => setTimeout(r, 700));
                return true;
              }}
            />
          </div>

          <div className="space-y-4">
            <OnboardingChecklist steps={onboardingSteps} />
            <OnboardingChecklist steps={[]} loading />
          </div>
        </div>
      </section>
    </div>
  );
}
