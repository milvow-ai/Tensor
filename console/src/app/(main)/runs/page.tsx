import { Activity } from "lucide-react";
import type { Metadata } from "next";

import { ComingSoon } from "@/components/farm/coming-soon";

export const metadata: Metadata = { title: "Runs" };

export default function RunsPage() {
  return (
    <ComingSoon
      title="Runs"
      description="Every request through the Farm, with the routing decisions behind it."
      milestone="C2"
      icon={Activity}
      will={[
        "Open any request and see the plan, each account tried, every fallback and why, the cost and the evidence links.",
        "Filter to failures and blocked calls to find what went wrong and which account caused it.",
        "Follow one request from the capability call to the final answer, event by event.",
      ]}
      today={{
        text: "The eight most recent runs, with result, account and cost, are on Overview. Each links to its run summary.",
        href: "/overview",
        label: "Open Overview",
      }}
    />
  );
}
