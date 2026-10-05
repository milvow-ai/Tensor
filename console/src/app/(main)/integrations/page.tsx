import { Blocks } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { getIntegrationsList } from "@/lib/farm/integrations-data";

import { IntegrationsView } from "./_components/integrations-view";

export const metadata: Metadata = {
  title: "Integrations",
  description: "Manage connected MCP servers, OpenAPI APIs, and AI CLI accounts.",
};

export default async function IntegrationsPage() {
  const integrations = await getIntegrationsList();

  return (
    <div className="space-y-6">
      <PageHeader
        title="Integrations"
        description="Every MCP server, OpenAPI provider, and AI account connected to Harness Farm."
        icon={Blocks}
      />
      <IntegrationsView initialIntegrations={integrations} />
    </div>
  );
}
