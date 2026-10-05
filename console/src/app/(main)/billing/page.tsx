import type { Metadata } from "next";
import Link from "next/link";
import { Receipt } from "lucide-react";

import { CommandsProvider } from "@/components/farm/commands-provider";
import { PageHeader } from "@/components/farm/page-header";
import { EmptyState, ErrorState } from "@/components/farm/states";
import { Button } from "@/components/ui/button";
import { attempt } from "@/lib/farm/data";

import { BillingKpiRow } from "./_components/billing-kpi-row";
import { BudgetsTable } from "./_components/budgets-table";
import { CostPerResultTable } from "./_components/cost-per-result-table";
import { IdlePaidCard } from "./_components/idle-paid-card";
import { RenewalCalendar } from "./_components/renewal-calendar";
import { SpendChartCard } from "./_components/spend-chart-card";

export const metadata: Metadata = { title: "Billing" };

export default async function BillingPage() {
  const result = await attempt(async (data) => {
    const [overview, pools] = await Promise.all([data.getBillingOverview(90), data.listPools()]);
    return { overview, pools };
  });

  if (!result.ok) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          title="Billing"
          description="Budgets, spend, projected month-end totals, renewals calendar, and idle accounts."
        />
        <ErrorState message={result.message} />
      </div>
    );
  }

  const { overview, pools } = result.data;

  if (pools.length === 0) {
    return (
      <CommandsProvider>
        <div className="flex flex-col gap-4 md:gap-5">
          <PageHeader
            title="Billing"
            description="Budgets, spend, projected month-end totals, renewals calendar, and idle accounts."
          />
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

  const totalSpendRow = overview.spendMonth.find((r) => r.provider_id === "total") ?? {
    provider_id: "total",
    provider_name: "Total",
    kind: "all",
    usage_usd: 0,
    billing_usd: 0,
    spend_usd: 0,
    budget_usd: null,
    forecast_usd: 0,
    elapsed_days: 1,
    days_in_month: 30,
  };

  return (
    <CommandsProvider>
      <div className="flex flex-col gap-4 md:gap-5">
        <PageHeader
          title="Billing"
          description="Budgets, spend, projected month-end totals, renewals calendar, and idle accounts."
        />

        {/* KPI Row */}
        <BillingKpiRow
          totalSpendRow={totalSpendRow}
          paidAccountsCount={overview.paidAccountsCount}
          idlePaidCount={overview.idlePaidCount}
        />

        {/* Spend Over Time Stacked Area Chart */}
        <SpendChartCard rows={overview.spendDaily} />

        {/* Budgets & Hard Stops + Idle Paid Accounts */}
        <div className="grid gap-4 md:gap-5 xl:grid-cols-2">
          <BudgetsTable budgets={overview.budgets} pools={pools} />
          <IdlePaidCard rows={overview.idlePaid} />
        </div>

        {/* Renewal Calendar */}
        <RenewalCalendar renewals={overview.renewals} />

        {/* Cost Per Result */}
        <CostPerResultTable rows={overview.costPerResult} />
      </div>
    </CommandsProvider>
  );
}
