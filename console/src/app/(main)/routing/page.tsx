import { GitFork } from "lucide-react";
import type { Metadata } from "next";

import { ComingSoon } from "@/components/farm/coming-soon";

export const metadata: Metadata = { title: "Routing" };

export default function RoutingPage() {
  return (
    <ComingSoon
      title="Routing"
      description="Which pools serve which capability, and in what order."
      milestone="C3"
      icon={GitFork}
      will={[
        "Reorder the pools a capability tries, and enable or disable a step.",
        "Set the default strategy per capability.",
        "Preview the route before saving it; changes go through the command queue like every other control.",
      ]}
      today={{
        text: "The current routes are drawn on Overview under How a request flows. A pool's own strategy can be changed on its page today.",
        href: "/overview",
        label: "Open Overview",
      }}
    />
  );
}
