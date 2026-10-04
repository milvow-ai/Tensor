import { notFound } from "next/navigation";

import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { SectionTitle } from "@/components/farm/section-title";
import { ErrorState } from "@/components/farm/states";
import { ToneBadge } from "@/components/farm/status";
import { RelativeTime } from "@/components/farm/time";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { attempt } from "@/lib/farm/data";
import { DASH, fmtAbsolute, fmtDuration, fmtUsd } from "@/lib/farm/format";
import { RUN_STATUS_META } from "@/lib/farm/state";

export const metadata: Metadata = { title: "Run" };

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="mt-0.5 break-words text-sm">{children}</dd>
    </div>
  );
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await attempt((data) => data.getRun(id));
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
            Started <RelativeTime iso={run.started_at} /> via {run.caller}
          </>
        }
        badge={<ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>}
      />
      <Card>
        <CardHeader>
          <SectionTitle>Summary</SectionTitle>
          <CardDescription>The outcome of this request as the Farm recorded it.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-4">
            <Item label="Pool">{run.provider_name ?? (run.cached ? "Served from cache" : DASH)}</Item>
            <Item label="Account">
              {run.connection_id ? (
                <>
                  <span className="font-mono">{run.connection_id}</span>
                  {run.connection_label ? (
                    <span className="text-muted-foreground"> · {run.connection_label}</span>
                  ) : null}
                </>
              ) : (
                DASH
              )}
            </Item>
            <Item label="Strategy">{run.strategy ?? DASH}</Item>
            <Item label="Attempts">{run.attempts_count}</Item>
            <Item label="Cost">{fmtUsd(run.cost_usd)}</Item>
            <Item label="Duration">{fmtDuration(run.duration_ms)}</Item>
            <Item label="Started">{fmtAbsolute(run.started_at, true)}</Item>
            <Item label="Finished">{fmtAbsolute(run.finished_at, true)}</Item>
          </dl>
          {run.error_kind ? (
            <div className="mt-5 rounded-lg border border-amber-600/25 bg-amber-500/5 p-3 text-sm">
              <div className="font-medium font-mono text-amber-800 dark:text-amber-400">{run.error_kind}</div>
              {run.error ? <p className="mt-1 text-muted-foreground">{run.error}</p> : null}
            </div>
          ) : null}
        </CardContent>
      </Card>
      <p className="text-muted-foreground text-sm">
        The event-by-event trajectory (plan, each account tried, fallbacks and evidence) arrives with the Runs page in
        C2.
      </p>
    </div>
  );
}
