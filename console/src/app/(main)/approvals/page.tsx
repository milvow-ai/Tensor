import type { Metadata } from "next";
import { ShieldCheck } from "lucide-react";

import { EmptyState } from "@/components/farm/states";
import { PageHeader } from "@/components/kit";

export const metadata: Metadata = { title: "Approvals" };

export default function ApprovalsPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Approvals"
        subtitle="Human-in-the-loop review queue for tool calls and actions."
      />
      <EmptyState
        icon={ShieldCheck}
        title="Arrives with delegation (A1)"
        description="Approvals lets you review, allow, or reject risky actions and high-cost tool calls requested by agents."
      />
    </div>
  );
}
