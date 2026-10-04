import type { Metadata } from "next";

import { CommandsFeed } from "@/components/farm/commands-feed";
import { PageHeader } from "@/components/farm/page-header";
import { ErrorState } from "@/components/farm/states";
import { attempt } from "@/lib/farm/data";
import { summarizeOverview } from "@/lib/farm/state";

import { AlertsCard } from "./_components/alerts-card";
import { CapacityCard } from "./_components/capacity-card";
import { groupCapacity } from "./_components/capacity-model";
import { FlowMap } from "./_components/flow-map";
import { KpiRow } from "./_components/kpi-row";
import { PoolHealthGrid } from "./_components/pool-health-grid";
import { RecentRunsCard } from "./_components/recent-runs-card";

export const metadata: Metadata = { title: "Overview" };

export default async function OverviewPage() {
  const result = await attempt((data) => data.getOverview());

  if (!result.ok) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title="Overview" description="Capacity, spend, health and alerts across every pool." />
        <ErrorState message={result.message} />
      </div>
    );
  }

  const overview = result.data;
  const summary = summarizeOverview(overview);
  const total = overview.spend.find((row) => row.provider_id === "total");

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title="Overview"
        description="Capacity, spend, health and alerts across every tool pool and AI pool."
      />
      <KpiRow summary={summary} daysInMonth={total?.days_in_month ?? 30} elapsedDays={total?.elapsed_days ?? 1} />
      <div className="grid gap-4 md:gap-5 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <CapacityCard rows={groupCapacity(overview.capacity)} />
        </div>
        <div className="xl:col-span-2">
          <AlertsCard alerts={overview.alerts} pools={overview.pools} />
        </div>
      </div>
      <PoolHealthGrid pools={overview.pools} />
      <div className="grid gap-4 md:gap-5 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <RecentRunsCard runs={overview.runs} />
        </div>
        <div className="xl:col-span-2">
          <CommandsFeed commands={overview.commands} className="h-full" />
        </div>
      </div>
      <FlowMap rows={overview.capacity} />
    </div>
  );
}
