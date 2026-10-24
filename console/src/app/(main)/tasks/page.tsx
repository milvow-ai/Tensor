import type { Metadata } from "next";
import { CheckCircle2 } from "lucide-react";

import { EmptyState } from "@/components/farm/states";
import { PageHeader } from "@/components/kit";

export const metadata: Metadata = { title: "Tasks" };

export default function TasksPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Tasks"
        subtitle="Delegated multi-step work across your AI team."
      />
      <EmptyState
        icon={CheckCircle2}
        title="Arrives with delegation (A1)"
        description="Tasks lets you dispatch, track, and review multi-step AI delegations with dependency graphs, step progress, and cost limits."
      />
    </div>
  );
}
