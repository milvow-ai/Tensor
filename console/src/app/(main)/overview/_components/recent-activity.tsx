import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { RelativeTime } from "@/components/farm/time";
import { type FarmStatus, StatusChip } from "@/components/kit";
import { fmtUsd } from "@/lib/farm/format";
import type { RunRow } from "@/lib/farm/types";

function runStatusToFarmStatus(status: RunRow["status"]): FarmStatus {
  switch (status) {
    case "succeeded":
      return "ready";
    case "running":
      return "running";
    case "failed":
      return "failed";
    case "blocked":
      return "waiting-for-you";
    default:
      return "ready";
  }
}

export function RecentActivity({ runs }: { runs: RunRow[] }) {
  return (
    <div className="flex flex-col space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22]">
      {/* Header */}
      <div className="flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b pb-3 dark:border-[#30363d]">
        <div className="space-y-0.5">
          <h2 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
            Recent activity
          </h2>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Latest tool calls, runs, and delegations.
          </p>
        </div>
        <Link
          href="/runs"
          className="inline-flex items-center gap-1 font-medium text-[var(--kit-accent,#1a63d6)] text-xs hover:underline dark:text-[#58a6ff]"
        >
          <span>View all</span>
          <ArrowUpRight className="size-3.5" />
        </Link>
      </div>

      {/* List */}
      <div className="flex-1 divide-y divide-[var(--kit-border,#e6e8ec)] overflow-y-auto dark:divide-[#30363d]">
        {runs.length === 0 ? (
          <div className="py-8 text-center text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            No recent runs recorded yet.
          </div>
        ) : (
          runs.slice(0, 10).map((run) => (
            <div
              key={run.id}
              className="flex items-center justify-between gap-3 py-2.5 text-xs transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)]/50 dark:hover:bg-[#21262d]/50"
            >
              <div className="flex min-w-0 items-center gap-2.5">
                <StatusChip status={runStatusToFarmStatus(run.status)} size="sm" />
                <div className="min-w-0">
                  <Link
                    href={`/runs/${run.id}`}
                    className="truncate font-semibold text-[var(--kit-text-primary,#111827)] hover:text-[var(--kit-accent,#1a63d6)] dark:text-[#f0f6fc] dark:hover:text-[#58a6ff]"
                  >
                    {run.capability}
                  </Link>
                  <div className="truncate text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
                    {run.caller} · {run.provider_name ?? "direct"}
                  </div>
                </div>
              </div>

              <div className="flex shrink-0 items-center gap-3 text-right">
                <div className="tabular-nums">
                  <div className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                    {run.cost_usd > 0 ? fmtUsd(run.cost_usd) : "free"}
                  </div>
                  <div className="text-[10px] text-[var(--kit-text-muted,#9ca3af)] dark:text-[#6e7681]">
                    {run.duration_ms !== null ? `${run.duration_ms} ms` : "in flight"}
                  </div>
                </div>
                <div className="hidden text-[11px] text-[var(--kit-text-secondary,#5b6370)] sm:block dark:text-[#8b949e]">
                  <RelativeTime iso={run.started_at} />
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
