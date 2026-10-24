import { ShieldCheck } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";

import { PoliciesView } from "./_components/policies-view";
import { loadPolicies } from "./_lib/load-policies";

export const metadata: Metadata = {
  title: "Policies & Budgets",
  description: "Global and per-provider spending caps, hard stop thresholds, and audit log.",
};

export default async function PoliciesPage() {
  const { budgets, spendMonth, auditEvents } = await loadPolicies();

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title="Policies & Budgets"
        description="Global spending caps, per-provider hard stops, alert thresholds, and security policies."
        icon={ShieldCheck}
      />
      <PoliciesView initialBudgets={budgets} spendMonth={spendMonth} initialAuditEvents={auditEvents} />
    </div>
  );
}
