import type { Metadata } from "next";

import { PageHeader } from "@/components/kit";
import { attempt } from "@/lib/farm/data";
import { getIntegrationsList } from "@/lib/farm/integrations-data";

import { IntegrationsView } from "./_components/integrations-view";

export const metadata: Metadata = {
  title: "Integrations",
  description: "Manage connected MCP servers, OpenAPI APIs, and AI CLI accounts.",
};

export default async function IntegrationsPage() {
  const result = await attempt(async (data) => {
    const [integrations, pools] = await Promise.all([getIntegrationsList(), data.listPools()]);
    const connections = await Promise.all(
      pools.map(async (pool) => (await data.listConnections(pool.provider_id, { pageSize: 50 })).items),
    );
    return { integrations, connections: connections.flat() };
  });

  if (!result.ok) {
    return <div className="space-y-5"><PageHeader title="Integrations" subtitle="MCP servers, OpenAPI providers, and AI accounts connected to Harness Farm." /><p role="alert" className="text-sm text-destructive">{result.message}</p></div>;
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Integrations" subtitle="MCP servers, OpenAPI providers, and AI accounts connected to Harness Farm." />
      <IntegrationsView initialIntegrations={result.data.integrations} connections={result.data.connections} />
    </div>
  );
}
