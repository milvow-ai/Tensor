import Link from "next/link";

import { ArrowRight, RotateCcw, Zap } from "lucide-react";

import { SectionTitle } from "@/components/farm/section-title";
import { EmptyState } from "@/components/farm/states";
import { ToneBadge } from "@/components/farm/status";
import { RelativeTime } from "@/components/farm/time";
import { Card, CardAction, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DASH, fmtDuration, fmtUsd } from "@/lib/farm/format";
import { RUN_STATUS_META } from "@/lib/farm/state";
import type { RunRow } from "@/lib/farm/types";

const LINK_CLASS =
  "font-medium font-mono text-[13px] after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-ring focus-visible:after:ring-inset";

function Result({ run }: { run: RunRow }) {
  const meta = RUN_STATUS_META[run.status];
  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>
        {run.cached ? (
          <span className="inline-flex items-center gap-1 text-muted-foreground text-xs">
            <Zap aria-hidden="true" className="size-3" /> cached
          </span>
        ) : null}
        {run.attempts_count > 1 ? (
          <span
            className="inline-flex items-center gap-1 text-muted-foreground text-xs"
            title="The first account failed; the router fell back"
          >
            <RotateCcw aria-hidden="true" className="size-3" /> {run.attempts_count} tries
          </span>
        ) : null}
      </div>
      {run.error_kind ? <div className="mt-0.5 font-mono text-muted-foreground text-xs">{run.error_kind}</div> : null}
    </>
  );
}

function Account({ run }: { run: RunRow }) {
  if (!run.provider_name) return <span className="text-muted-foreground">{run.cached ? "from cache" : DASH}</span>;
  return (
    <>
      <div className="text-[13px]">{run.provider_name}</div>
      <div className="font-mono text-muted-foreground text-xs">{run.connection_id}</div>
    </>
  );
}

function accountLabel(run: RunRow): string {
  if (run.provider_name) return `${run.provider_name} · ${run.connection_id}`;
  return run.cached ? "from cache" : DASH;
}

function Cost({ value }: { value: number }) {
  return value > 0 ? fmtUsd(value) : <span className="text-muted-foreground">{fmtUsd(0)}</span>;
}

export function RecentRunsCard({ runs }: { runs: RunRow[] }) {
  return (
    <Card className="gap-3">
      <CardHeader>
        <SectionTitle>Recent runs</SectionTitle>
        <CardDescription>
          The last requests through the Farm: what was asked, who answered, what it cost.
        </CardDescription>
        <CardAction>
          <Link
            href="/runs"
            className="inline-flex items-center gap-1 rounded text-muted-foreground text-xs hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          >
            All runs <ArrowRight aria-hidden="true" className="size-3" />
          </Link>
        </CardAction>
      </CardHeader>
      <CardContent>
        {runs.length === 0 ? (
          <EmptyState
            icon={Zap}
            title="No runs yet"
            description="Requests appear here as soon as Claude or Hermes call a capability through the Farm."
          />
        ) : (
          <>
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Capability</TableHead>
                    <TableHead>Result</TableHead>
                    <TableHead>Pool / account</TableHead>
                    <TableHead className="text-right">Cost</TableHead>
                    <TableHead className="hidden text-right lg:table-cell">Took</TableHead>
                    <TableHead className="text-right">When</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runs.map((run) => (
                    <TableRow key={run.id} className="relative">
                      <TableCell>
                        <Link href={`/runs/${run.id}`} className={LINK_CLASS}>
                          {run.capability}
                        </Link>
                        <div className="text-muted-foreground text-xs">via {run.caller}</div>
                      </TableCell>
                      <TableCell>
                        <Result run={run} />
                      </TableCell>
                      <TableCell>
                        <Account run={run} />
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        <Cost value={run.cost_usd} />
                      </TableCell>
                      <TableCell className="hidden text-right text-muted-foreground tabular-nums lg:table-cell">
                        {fmtDuration(run.duration_ms)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground tabular-nums">
                        <RelativeTime iso={run.started_at} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="divide-y md:hidden">
              {runs.map((run) => (
                <li key={run.id} className="relative flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
                  <div className="flex items-baseline justify-between gap-3">
                    <Link href={`/runs/${run.id}`} className={LINK_CLASS}>
                      {run.capability}
                    </Link>
                    <span className="text-muted-foreground text-xs tabular-nums">
                      <RelativeTime iso={run.started_at} />
                    </span>
                  </div>
                  <div>
                    <Result run={run} />
                  </div>
                  <div className="flex items-baseline justify-between gap-3 text-xs">
                    <span className="text-muted-foreground">{accountLabel(run)}</span>
                    <span className="tabular-nums">
                      <Cost value={run.cost_usd} />
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}
