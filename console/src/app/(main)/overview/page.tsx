import type { Metadata } from "next";
import Link from "next/link";
import { Activity, CheckCircle2, DollarSign, Server } from "lucide-react";

import { EmptyState, ErrorState } from "@/components/farm/states";
import {
  BreakdownCard,
  InsightsPanel,
  MetricCard,
  PageHeader,
  TrendChartCard,
} from "@/components/kit";
import { Button } from "@/components/ui/button";
import { attempt } from "@/lib/farm/data";
import { fmtUsd } from "@/lib/farm/format";
import { summarizeOverview } from "@/lib/farm/state";

import { AlertsCard } from "./_components/alerts-card";
import { NeedsYouBanner } from "./_components/needs-you-banner";
import { RecentActivity } from "./_components/recent-activity";
import { RecentFacts } from "./_components/recent-facts";

export const metadata: Metadata = { title: "Overview" };

const PROVIDER_COLORS = [
  "var(--kit-accent, #1f6feb)",
  "var(--kit-category-2, #0284c7)",
  "var(--kit-category-3, #9333ea)",
  "var(--kit-category-4, #ea580c)",
  "var(--kit-category-5, #10b981)",
  "var(--kit-category-6, #eab308)",
];

export default async function OverviewPage() {
  const [overviewResult, spendDailyResult, factsResult] = await Promise.all([
    attempt((data) => data.getOverview()),
    attempt((data) => data.listSpendDaily(14)),
    attempt((data) => data.listFacts({ pageSize: 5 })),
  ]);

  if (!overviewResult.ok) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          title="Overview"
          subtitle="Capacity, spend, health and alerts across every pool."
        />
        <ErrorState message={overviewResult.message} />
      </div>
    );
  }

  const overview = overviewResult.data;

  if (overview.pools.length === 0) {
    return (
      <div className="flex flex-col gap-4 md:gap-5">
        <PageHeader
          title="Overview"
          subtitle="Capacity, spend, health and alerts across every tool pool and AI pool."
        />
        <EmptyState
          icon={Server}
          title="Welcome to your Farm"
          description="Your Farm starts blank with zero providers. Connect your first MCP server, AI CLI account, or OpenAPI provider to see live capacity and routing."
        >
          <Button asChild>
            <Link href="/integrations">Add your first MCP server</Link>
          </Button>
        </EmptyState>
      </div>
    );
  }

  const summary = summarizeOverview(overview);
  const budgetRatio = summary.budget && summary.budget > 0 ? summary.spend / summary.budget : null;
  const accountsNeedsLogin = overview.pools.reduce((sum, p) => sum + p.accounts_needs_login, 0);

  const succeededRuns = overview.runs.filter((r) => r.status === "succeeded").length;
  const successRate = overview.runs.length > 0 ? (succeededRuns / overview.runs.length) * 100 : 100;
  const totalMonthlyPlan = overview.pools.reduce((sum, p) => sum + (p.monthly_plan_usd ?? 0), 0);

  // Chart data from spendDaily
  const spendDaily = spendDailyResult.ok ? spendDailyResult.data : [];
  const daysMap = new Map<string, Record<string, number>>();
  for (const row of spendDaily) {
    let dayEntry = daysMap.get(row.day);
    if (!dayEntry) {
      dayEntry = {};
      daysMap.set(row.day, dayEntry);
    }
    dayEntry[row.provider_id] = (dayEntry[row.provider_id] ?? 0) + row.spend_usd;
  }

  const sortedDays = Array.from(daysMap.keys()).sort();
  const chartData = sortedDays.map((day) => {
    const d = new Date(day);
    const formattedDate = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
    return {
      date: formattedDate,
      ...daysMap.get(day),
    };
  });

  const uniqueProviders = Array.from(new Set(spendDaily.map((row) => row.provider_id)));
  const chartSeries = uniqueProviders.slice(0, 6).map((providerId, index) => {
    const provider = overview.pools.find((p) => p.provider_id === providerId);
    return {
      key: providerId,
      name: provider?.provider_name ?? providerId,
      color: PROVIDER_COLORS[index % PROVIDER_COLORS.length],
    };
  });

  // Resource breakdown tabs
  const aiPools = overview.pools.filter((p) => p.kind === "ai");
  const totalAiAccounts = aiPools.reduce((sum, p) => sum + p.accounts_total, 0) || 1;
  const aiRows = aiPools.map((pool, idx) => ({
    label: pool.provider_name,
    value: `${pool.accounts_active} / ${pool.accounts_total} active`,
    share: Math.round((pool.accounts_total / totalAiAccounts) * 100),
    color: PROVIDER_COLORS[idx % PROVIDER_COLORS.length],
    sublabel: `${pool.health}`,
  }));

  const toolPools = overview.pools.filter((p) => p.kind === "tool");
  const totalToolAccounts = toolPools.reduce((sum, p) => sum + p.accounts_total, 0) || 1;
  const toolRows = toolPools.map((pool, idx) => ({
    label: pool.provider_name,
    value: `${pool.accounts_active} / ${pool.accounts_total} active`,
    share: Math.round((pool.accounts_total / totalToolAccounts) * 100),
    color: PROVIDER_COLORS[(idx + 2) % PROVIDER_COLORS.length],
    sublabel: `${pool.health}`,
  }));

  const healthRows = [
    {
      label: "Healthy",
      value: `${summary.poolsHealthy} pools`,
      share: Math.round((summary.poolsHealthy / (summary.poolsTotal || 1)) * 100),
      color: "var(--kit-positive, #16a34a)",
    },
    {
      label: "Degraded",
      value: `${summary.poolsDegraded} pools`,
      share: Math.round((summary.poolsDegraded / (summary.poolsTotal || 1)) * 100),
      color: "var(--kit-warning, #f59e0b)",
    },
    {
      label: "Down",
      value: `${summary.poolsDown} pools`,
      share: Math.round((summary.poolsDown / (summary.poolsTotal || 1)) * 100),
      color: "var(--kit-negative, #dc2626)",
    },
  ];

  const breakdownTabs = [
    { id: "ai", label: "By AI", rows: aiRows },
    { id: "tools", label: "By Tool", rows: toolRows },
    { id: "health", label: "By Health", rows: healthRows },
  ];

  const healthSplit = [
    {
      label: "Healthy",
      share: Math.round((summary.poolsHealthy / (summary.poolsTotal || 1)) * 100),
      color: "var(--kit-positive, #16a34a)",
    },
    {
      label: "Degraded",
      share: Math.round((summary.poolsDegraded / (summary.poolsTotal || 1)) * 100),
      color: "var(--kit-warning, #f59e0b)",
    },
    {
      label: "Down",
      share: Math.round((summary.poolsDown / (summary.poolsTotal || 1)) * 100),
      color: "var(--kit-negative, #dc2626)",
    },
  ];

  const healthMetricsRows = [
    { label: "Routable accounts", value: summary.accountsUsable },
    {
      label: "Paused or blocked",
      value: summary.accountsTotal - summary.accountsActive,
    },
    { label: "Accounts needing login", value: accountsNeedsLogin },
    {
      label: "Monthly subscriptions",
      value: fmtUsd(totalMonthlyPlan, { whole: true }),
    },
  ];

  const facts = factsResult.ok ? factsResult.data.items : [];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Overview"
        subtitle="Capacity, spend, health and alerts across your Farm."
      />

      {/* Needs you banner */}
      <NeedsYouBanner
        accountsNeedsLogin={accountsNeedsLogin}
        openAlertsCount={summary.openAlerts}
        budgetRatio={budgetRatio}
      />

      {/* Row 1: KPI Tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Spend this month"
          value={summary.spend}
          format="usd"
          comparison={
            summary.budget
              ? `of $${summary.budget.toLocaleString()} budget`
              : `Forecast $${Math.round(summary.forecast).toLocaleString()}`
          }
          href="/usage"
          icon={<DollarSign className="size-4" />}
          delta={{ value: -9.1, goodDirection: "down" }}
        />
        <MetricCard
          label="Healthy pools"
          value={`${summary.poolsHealthy} / ${summary.poolsTotal}`}
          comparison={`${summary.poolsDegraded} degraded · ${summary.poolsDown} down`}
          href="/pools/tools"
          icon={<Server className="size-4" />}
        />
        <MetricCard
          label="Active accounts"
          value={`${summary.accountsActive} / ${summary.accountsTotal}`}
          comparison={`${summary.accountsUsable} routable right now`}
          href="/pools/ai"
          icon={<Activity className="size-4" />}
        />
        <MetricCard
          label="Recent run success"
          value={Math.round(successRate)}
          format="percent"
          comparison={`across last ${overview.runs.length} runs`}
          href="/runs"
          icon={<CheckCircle2 className="size-4" />}
        />
      </div>

      {/* Row 2: TrendChartCard & BreakdownCard */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <TrendChartCard
            title="Usage over time"
            value={`$${summary.spend.toLocaleString()}`}
            series={chartSeries}
            data={chartData}
            metric="USD"
            filters={[{ value: "spend", label: "Spend ($)" }]}
            selectedFilter="spend"
          />
        </div>
        <div className="xl:col-span-1">
          <BreakdownCard
            title="Resource mix"
            tabs={breakdownTabs}
            totalValue={`${summary.accountsTotal} accounts`}
            viewAllHref="/pools/ai"
          />
        </div>
      </div>

      {/* Row 3: Farm Health Insights & AlertsCard */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <InsightsPanel
          header={{
            title: "Farm health",
            meta: `${summary.accountsUsable} of ${summary.accountsTotal} accounts ready`,
          }}
          rows={healthMetricsRows}
          split={healthSplit}
          splitTitle="Pool health distribution"
        />
        <AlertsCard alerts={overview.alerts} pools={overview.pools} />
      </div>

      {/* Row 4: Recent Activity & Recently Learned */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <RecentActivity runs={overview.runs} />
        </div>
        <div className="xl:col-span-1">
          <RecentFacts facts={facts} />
        </div>
      </div>
    </div>
  );
}
