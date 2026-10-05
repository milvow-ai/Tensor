import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { ErrorState } from "@/components/farm/states";
import { attempt } from "@/lib/farm/data";

import { RunsTable } from "./_components/runs-table";

export const metadata: Metadata = { title: "Runs" };

export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const capability = typeof params.capability === "string" ? params.capability : undefined;
  const status = typeof params.status === "string" ? params.status : undefined;
  const caller = typeof params.caller === "string" ? params.caller : undefined;
  const failuresOnly = params.failures_only === "true";
  const page = typeof params.page === "string" ? Math.max(1, Number.parseInt(params.page, 10) || 1) : 1;

  const result = await attempt(async (data) => {
    const [runsPage, overview] = await Promise.all([
      data.listRuns({
        capability,
        status,
        caller,
        failuresOnly,
        page,
        pageSize: 20,
      }),
      data.getOverview(),
    ]);

    // Unique capability names from capacity routes
    const capSet = new Set<string>();
    for (const c of overview.capacity) {
      capSet.add(c.capability);
    }
    for (const r of overview.runs) {
      capSet.add(r.capability);
    }
    const capabilities = Array.from(capSet).sort();

    return { runsPage, capabilities };
  });

  if (!result.ok) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader
          title="Runs"
          description="Every capability request through the Farm, with routing decisions and execution trajectories."
        />
        <ErrorState message={result.message} />
      </div>
    );
  }

  const { runsPage, capabilities } = result.data;

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title="Runs"
        description="Every capability request through the Farm, with routing decisions, fallbacks, and evidence links."
      />
      <RunsTable runsPage={runsPage} capabilities={capabilities} />
    </div>
  );
}
