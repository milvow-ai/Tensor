import type { Metadata } from "next";

import { PageHeader } from "@/components/kit";
import { getFarmData } from "@/lib/farm/data";
import { getAiConversationsList, getAiJobsList } from "@/lib/farm/integrations-data";

import { AiOrchestrationView } from "../pools/ai/_components/ai-orchestration-view";

export const metadata: Metadata = { title: "Tasks" };

export default async function TasksPage() {
  const data = await getFarmData();
  const [jobs, conversations, pools] = await Promise.all([
    getAiJobsList(),
    getAiConversationsList(),
    data.listPools("ai"),
  ]);
  const pages = await Promise.all(pools.map((pool) => data.listConnections(pool.provider_id, { pageSize: 50 })));
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Tasks" subtitle="Your worker jobs, conversations, and cancellation controls." />
      <AiOrchestrationView
        initialJobs={jobs}
        initialConversations={conversations}
        accounts={pages.flatMap((page) => page.items)}
      />
    </div>
  );
}
