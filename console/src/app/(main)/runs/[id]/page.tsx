import { notFound } from "next/navigation";

import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { ErrorState } from "@/components/farm/states";
import { StatusChip } from "@/components/kit";
import { RelativeTime } from "@/components/farm/time";
import { attempt } from "@/lib/farm/data";

import { RunDetailView } from "../_components/run-detail-view";
import { RUN_CHIP } from "../_components/run-status";

export const metadata: Metadata = { title: "Activity trace" };

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await attempt((data) => data.getRunDetail(id));
  const crumbs = [{ label: "Activity", href: "/runs" }, { label: id.slice(0, 8) }];

  if (!result.ok) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title="Run" crumbs={crumbs} />
        <ErrorState message={result.message} />
      </div>
    );
  }

  const run = result.data;
  if (!run) notFound();

  const chip = RUN_CHIP[run.status];

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title={run.capability}
        crumbs={crumbs}
        description={
          <>
            Started <RelativeTime iso={run.started_at} /> via{" "}
            <span className="font-medium font-mono">{run.caller}</span>
          </>
        }
        badge={<StatusChip size="sm" status={chip.status} label={chip.label} />}
      />

      <RunDetailView run={run} />
    </div>
  );
}
