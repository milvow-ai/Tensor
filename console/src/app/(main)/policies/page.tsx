import { ShieldCheck } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { getFarmData } from "@/lib/farm/data";

import { PoliciesView } from "./_components/policies-view";

export const metadata: Metadata = {
  title: "Policies & Budgets",
  description: "Global and per-provider spending caps, hard stop thresholds, and audit log.",
};

export default async function PoliciesPage() {
  const farmData = await getFarmData();
  const [budgets, overview, auditPage] = await Promise.all([
    farmData.listBudgets(),
    farmData.getOverview(),
    farmData.listAuditEvents({ pageSize: 50 }),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Policies & Budgets"
        description="Global spending caps, per-provider hard stops, alert thresholds, and security policies."
        icon={ShieldCheck}
      />
      <PoliciesView initialBudgets={budgets} spendMonth={overview.spend} initialAuditEvents={auditPage.items} />
    </div>
  );
}
