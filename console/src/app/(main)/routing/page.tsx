import { GitFork } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";

import { RoutingView } from "./_components/routing-view";
import { loadRouting } from "./_lib/load-routing";

export const metadata: Metadata = {
  title: "Routing",
  description: "Configure pool order, strategies, and cache TTL per capability.",
};

export default async function RoutingPage() {
  const { routes, accountsByPool } = await loadRouting();

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title="Routing"
        description="Which pools serve each capability, in what order, and with what default strategy."
        icon={GitFork}
      />
      <RoutingView initialRoutes={routes} accountsByPool={accountsByPool} />
    </div>
  );
}
