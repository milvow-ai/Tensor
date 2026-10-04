import { Blocks } from "lucide-react";
import type { Metadata } from "next";

import { ComingSoon } from "@/components/farm/coming-soon";

export const metadata: Metadata = { title: "Integrations" };

export default function IntegrationsPage() {
  return (
    <ComingSoon
      title="Integrations"
      description="Connect new tools and APIs to the Farm."
      milestone="C3"
      icon={Blocks}
      will={[
        "Add an MCP server or REST API from a form generated from the registry schema.",
        "Map its tools to Farm capabilities.",
        "Run Test connection and see the result before the integration goes live.",
      ]}
      today={{
        text: "New accounts for an existing pool can be added now from the pool page.",
        href: "/pools/tools",
        label: "Open Tools & Pools",
      }}
    />
  );
}
