import { getFarmData } from "@/lib/farm/data";

/** Budgets, month spend and audit events: shared by `/policies` and the Settings → Policies & budgets tab. */
export async function loadPolicies() {
  const farmData = await getFarmData();
  const [budgets, overview, auditPage] = await Promise.all([
    farmData.listBudgets(),
    farmData.getOverview(),
    farmData.listAuditEvents({ pageSize: 50 }),
  ]);
  return { budgets, spendMonth: overview.spend, auditEvents: auditPage.items };
}
