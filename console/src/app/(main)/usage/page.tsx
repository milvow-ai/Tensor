import type { Metadata } from "next";
import Link from "next/link";

import { Receipt } from "lucide-react";

import { CommandsProvider } from "@/components/farm/commands-provider";
import { PageHeader } from "@/components/farm/page-header";
import { EmptyState, ErrorState } from "@/components/farm/states";
import { Button } from "@/components/ui/button";
import { attempt } from "@/lib/farm/data";

import { BudgetsTable } from "../billing/_components/budgets-table";
import { CostPerResultTable } from "../billing/_components/cost-per-result-table";
import { IdlePaidCard } from "../billing/_components/idle-paid-card";
import { RenewalCalendar } from "../billing/_components/renewal-calendar";
import { UsageBreakdown } from "./_components/usage-breakdown";
import { UsageKpis } from "./_components/usage-kpis";
import { UsageTrend } from "./_components/usage-trend";
import { buildUsage, parsePeriod } from "./_lib/usage-model";

export const metadata: Metadata = { title: "Usage & Cost" };

const DESCRIPTION = "Which AI and tools spent what, with budgets, forecast, renewals and idle accounts.";

export default async function UsagePage({ searchParams }: { searchParams: Promise<{ period?: string }> }) {
  const period = parsePeriod((await searchParams).period);

  const result = await attempt(async (data) => {
    const [overview, pools] = await Promise.all([data.getBillingOverview(90), data.listPools()]);
    return { overview, pools };
  });

  if (!result.ok) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title="Usage & Cost" description={DESCRIPTION} />
        <ErrorState message={result.message} />
      </div>
    );
  }

  const { overview, pools } = result.data;

  if (pools.length === 0) {
    return (
      <CommandsProvider>
        <div className="flex flex-col gap-4 md:gap-5">
          <PageHeader title="Usage & Cost" description={DESCRIPTION} />
          <EmptyState
            icon={Receipt}
            title="No billing activity yet"
            description="Budgets, spend, and renewal forecasts will appear here once you connect providers and accounts."
          >
            <Button asChild>
              <Link href="/integrations">Add your first MCP server</Link>
            </Button>
          </EmptyState>
        </div>
      </CommandsProvider>
    );
  }

  const usage = buildUsage(overview, pools, period);

  return (
    <CommandsProvider>
      <div className="flex flex-col gap-4 md:gap-5">
        <PageHeader title="Usage & Cost" description={DESCRIPTION} />

        <UsageKpis usage={usage} paidAccountsCount={overview.paidAccountsCount} idlePaidCount={overview.idlePaidCount} />

        <div className="grid gap-4 md:gap-5 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <UsageTrend usage={usage} />
          </div>
          <UsageBreakdown usage={usage} />
        </div>

        <div className="grid gap-4 md:gap-5 xl:grid-cols-2">
          <BudgetsTable budgets={overview.budgets} pools={pools} />
          <IdlePaidCard rows={overview.idlePaid} />
        </div>

        <RenewalCalendar renewals={overview.renewals} />

        <CostPerResultTable rows={overview.costPerResult} />
      </div>
    </CommandsProvider>
  );
}
