import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { ErrorState } from "@/components/farm/states";
import { attempt } from "@/lib/farm/data";
import type { Page, RunRow } from "@/lib/farm/types";

import { RunsTable } from "./_components/runs-table";

export const metadata: Metadata = { title: "Activity" };

const DESCRIPTION = "Every call through the Farm in one stream: what ran, on which pool, the outcome, time and cost.";
const PAGE_SIZE = 20;

export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const capability = typeof params.capability === "string" ? params.capability : undefined;
  const status = typeof params.status === "string" ? params.status : undefined;
  const caller = typeof params.caller === "string" ? params.caller : undefined;
  const pool = typeof params.pool === "string" ? params.pool : undefined;
  const failuresOnly = params.failures_only === "true";
  const page = typeof params.page === "string" ? Math.max(1, Number.parseInt(params.page, 10) || 1) : 1;

  const result = await attempt(async (data) => {
    // The Farm has no pool filter on runs; with one set, read a wide window and filter + paginate here.
    const [fetched, overview] = await Promise.all([
      data.listRuns({
        capability,
        status,
        caller,
        failuresOnly,
        page: pool ? 1 : page,
        pageSize: pool ? 500 : PAGE_SIZE,
      }),
      data.getOverview(),
    ]);

    let runsPage: Page<RunRow> = fetched;
    if (pool) {
      const matching = fetched.items.filter((r) => (r.provider_name ?? (r.cached ? "Cache" : "")) === pool);
      runsPage = {
        ...fetched,
        items: matching.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
        total: matching.length,
        page,
        pageSize: PAGE_SIZE,
      };
    }

    const capSet = new Set<string>();
    const poolSet = new Set<string>();
    for (const c of overview.capacity) capSet.add(c.capability);
    for (const r of overview.runs) {
      capSet.add(r.capability);
      if (r.provider_name) poolSet.add(r.provider_name);
    }
    for (const r of fetched.items) {
      if (r.provider_name) poolSet.add(r.provider_name);
    }
    if (pool) poolSet.add(pool);

    return { runsPage, capabilities: Array.from(capSet).sort(), pools: Array.from(poolSet).sort() };
  });

  if (!result.ok) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader title="Activity" description={DESCRIPTION} />
        <ErrorState message={result.message} />
      </div>
    );
  }

  const { runsPage, capabilities, pools } = result.data;

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader title="Activity" description={DESCRIPTION} />
      <RunsTable runsPage={runsPage} capabilities={capabilities} pools={pools} />
    </div>
  );
}
