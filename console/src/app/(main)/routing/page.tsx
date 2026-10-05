import { GitFork } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { getFarmData } from "@/lib/farm/data";

import { RoutingView } from "./_components/routing-view";

export const metadata: Metadata = {
  title: "Routing",
  description: "Configure pool order, strategies, and cache TTL per capability.",
};

export default async function RoutingPage() {
  const farmData = await getFarmData();
  const routes = await farmData.listRoutes();

  // Load account previews for all unique pools in routes
  const poolIds = Array.from(new Set(routes.map((r) => r.provider_id)));
  const accountsByPool: Record<
    string,
    Array<{
      id: string;
      label: string;
      status: string;
      remaining: number | null;
      unit: string | null;
      latencyMs: number | null;
      circuit: string;
    }>
  > = {};

  await Promise.all(
    poolIds.map(async (poolId) => {
      const page = await farmData.listConnections(poolId, { pageSize: 50 });
      accountsByPool[poolId] = page.items.map((conn) => ({
        id: conn.id,
        label: conn.label,
        status: conn.status,
        remaining: conn.units[0]?.remaining ?? null,
        unit: conn.units[0]?.unit ?? null,
        latencyMs: conn.health.latencyMsP50,
        circuit: conn.health.circuit,
      }));
    }),
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Routing"
        description="Which pools serve each capability, in what order, and with what default strategy."
        icon={GitFork}
      />
      <RoutingView initialRoutes={routes} accountsByPool={accountsByPool} />
    </div>
  );
}
