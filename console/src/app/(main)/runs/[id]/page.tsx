import { notFound } from "next/navigation";

import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { ErrorState } from "@/components/farm/states";
import { ToneBadge } from "@/components/farm/status";
import { RelativeTime } from "@/components/farm/time";
import { attempt } from "@/lib/farm/data";
import { RUN_STATUS_META } from "@/lib/farm/state";

import { RunDetailView } from "../_components/run-detail-view";

export const metadata: Metadata = { title: "Run Detail" };

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await attempt((data) => data.getRunDetail(id));
  const crumbs = [{ label: "Runs", href: "/runs" }, { label: id.slice(0, 8) }];

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

  const meta = RUN_STATUS_META[run.status];

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title={run.capability}
        crumbs={crumbs}
        description={
          <>
            Started <RelativeTime iso={run.started_at} /> via{" "}
            <span className="font-mono font-medium">{run.caller}</span>
          </>
        }
        badge={<ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>}
      />

      <RunDetailView run={run} />
    </div>
  );
}
