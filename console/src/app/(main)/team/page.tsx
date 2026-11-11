import { Users } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { requireOwner } from "@/lib/auth";
import { readTeamReports } from "@/lib/farm/team-reporting";

export const metadata: Metadata = { title: "Team usage" };

export default async function TeamPage() {
  await requireOwner();
  const reports = await readTeamReports();
  return (
    <div className="space-y-6">
      <PageHeader
        title="Team usage"
        description="Usage across your private Farms, with separate reporting access."
        icon={Users}
      />
      {reports.length === 0 ? (
        <p className="text-muted-foreground">No team Farms are connected for reporting yet.</p>
      ) : null}
      <div className="grid gap-4 md:grid-cols-2">
        {reports.map(({ name, snapshot, error }) => (
          <section key={name} className="space-y-3 rounded-xl border p-5">
            <h2 className="font-semibold">{name}</h2>
            {snapshot ? (
              <>
                <p>
                  {snapshot.ai_accounts} AI accounts · {snapshot.mcp_accounts} MCP accounts · {snapshot.other_accounts}{" "}
                  other accounts
                </p>
                <p>
                  {snapshot.jobs.running ?? 0} running · {snapshot.jobs.queued ?? 0} queued ·{" "}
                  {snapshot.jobs.failed ?? 0} failed this month
                </p>
                <p>${snapshot.recorded_cost_usd.toFixed(4)} recorded spend</p>
                <p className="text-muted-foreground text-sm">
                  {snapshot.unpriced_jobs} jobs with estimated pricing · {snapshot.missing_metering_jobs} missing
                  metering
                </p>
                <p className="text-muted-foreground text-xs">
                  Observed {snapshot.observed_at} · calendar {snapshot.timezone}
                  {Date.now() - Date.parse(snapshot.observed_at) > 60000 ? " · Stale" : ""}
                </p>
              </>
            ) : (
              <p role="status" className="text-muted-foreground">
                {error}
              </p>
            )}
          </section>
        ))}
      </div>
    </div>
  );
}
