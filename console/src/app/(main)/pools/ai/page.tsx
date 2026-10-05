import type { Metadata } from "next";

import { getFarmData } from "@/lib/farm/data";
import { getAiConversationsList, getAiJobsList } from "@/lib/farm/integrations-data";
import type { Connection } from "@/lib/farm/types";

import { PoolsIndex } from "../_components/pools-index";
import { AiOrchestrationView } from "./_components/ai-orchestration-view";

export const metadata: Metadata = {
  title: "AI Pools & Jobs",
  description: "AI CLI accounts, non-blocking jobs, conversation trajectories, and concurrency limits.",
};

export default async function AiPoolsPage() {
  const farmData = await getFarmData();
  const [jobs, conversations, pools] = await Promise.all([
    getAiJobsList(),
    getAiConversationsList(),
    farmData.listPools("ai"),
  ]);

  const aiAccounts: Connection[] = [];
  await Promise.all(
    pools.map(async (p) => {
      const page = await farmData.listConnections(p.provider_id, { pageSize: 50 });
      aiAccounts.push(...page.items);
    }),
  );

  return (
    <div className="space-y-8">
      <PoolsIndex kind="ai" />
      <div className="pt-2">
        <AiOrchestrationView initialJobs={jobs} initialConversations={conversations} accounts={aiAccounts} />
      </div>
    </div>
  );
}
