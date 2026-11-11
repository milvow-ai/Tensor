import { Database } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { getFarmData } from "@/lib/farm/data";

import { MemoryView } from "./_components/memory-view";
import { ProjectNotes } from "./_components/project-notes";

export const metadata: Metadata = {
  title: "Memory & Evidence",
  description: "Browse verified entity facts, provenance, and captured evidence artifacts.",
};

export default async function MemoryPage({
  searchParams,
}: {
  searchParams: Promise<{ project?: string | string[]; after?: string | string[] }>;
}) {
  const query = await searchParams;
  const farmData = await getFarmData();
  const [factsPage, evidencePage] = await Promise.all([
    farmData.listFacts({ pageSize: 100 }),
    farmData.listEvidence({ pageSize: 100 }),
  ]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Memory & Evidence"
        description="What the Farm has learned, facts with source provenance, and verifiable evidence."
        icon={Database}
      />
      <MemoryView initialFacts={factsPage.items} initialEvidence={evidencePage.items} />
      <ProjectNotes
        project={typeof query.project === "string" ? query.project : ""}
        after={typeof query.after === "string" ? query.after : ""}
      />
    </div>
  );
}
