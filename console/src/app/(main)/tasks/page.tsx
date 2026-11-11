import type { Metadata } from "next";

import { PageHeader } from "@/components/kit";
import { getFarmData } from "@/lib/farm/data";
import { getAiConversationsList, getAiJobsList } from "@/lib/farm/integrations-data";
import { getTaskOverview } from "@/lib/farm/task-data";

import { AiOrchestrationView } from "../pools/ai/_components/ai-orchestration-view";
import { TaskOverview } from "./_components/task-overview";

export const metadata: Metadata = { title: "Tasks" };

export default async function TasksPage() {
  const data = await getFarmData();
  const [jobs, conversations, pools, overview] = await Promise.all([
    getAiJobsList(),
    getAiConversationsList(),
    data.listPools("ai"),
    getTaskOverview(),
  ]);
  const pages = await Promise.all(pools.map((pool) => data.listConnections(pool.provider_id, { pageSize: 50 })));
  const accounts = pages.flatMap((page) => page.items);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Tasks" subtitle="Project handoffs, worker activity, and conversations." />
      <TaskOverview {...overview} accounts={accounts} now={Date.now()} />
      <AiOrchestrationView initialJobs={jobs} initialConversations={conversations} accounts={accounts} />
    </div>
  );
}
