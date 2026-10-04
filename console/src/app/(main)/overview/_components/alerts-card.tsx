"use client";

import Link from "next/link";

import { cn } from "cn";
import { CheckCheck, CircleAlert, Info, OctagonAlert } from "lucide-react";

import { CommandChip } from "@/components/farm/command-chip";
import { SectionTitle } from "@/components/farm/section-title";
import { EmptyState } from "@/components/farm/states";
import { ToneBadge } from "@/components/farm/status";
import { RelativeTime } from "@/components/farm/time";
import { useCommands } from "@/components/farm/use-command";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { poolHref, SEVERITY_META, TONE } from "@/lib/farm/state";
import type { AlertRow, AlertSeverity, PoolOverviewRow } from "@/lib/farm/types";

const ICONS: Record<AlertSeverity, typeof Info> = { critical: OctagonAlert, warn: CircleAlert, info: Info };

/** `clay-07` belongs to the `clay` pool; a pool id links to the pool itself. */
function refLink(ref: string | null, pools: Pick<PoolOverviewRow, "provider_id" | "kind">[]): string | null {
  if (!ref) return null;
  const pool = pools.find((candidate) => ref === candidate.provider_id || ref.startsWith(`${candidate.provider_id}-`));
  return pool ? poolHref(pool) : null;
}

function RefLabel({ reference, href }: { reference: string | null; href: string | null }) {
  if (!reference) return null;
  if (!href) return <span className="font-mono">{reference}</span>;
  return (
    <Link
      href={href}
      className="rounded font-mono underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring"
    >
      {reference}
    </Link>
  );
}

export function AlertsCard({
  alerts,
  pools,
}: {
  alerts: AlertRow[];
  pools: Pick<PoolOverviewRow, "provider_id" | "kind">[];
}) {
  const { pending, run } = useCommands();
  const open = alerts.filter((alert) => alert.acked_at === null);

  return (
    <Card className="gap-3" data-testid="alerts-card">
      <CardHeader>
        <SectionTitle>Alerts</SectionTitle>
        <CardDescription>What needs a human, most urgent first.</CardDescription>
        <CardAction>
          <span data-testid="alerts-open" className="text-muted-foreground text-xs tabular-nums">
            {open.length} open
          </span>
        </CardAction>
      </CardHeader>
      <CardContent>
        {open.length === 0 ? (
          <EmptyState
            icon={CheckCheck}
            title="All clear"
            description="No open alerts. The Farm raises one when an account needs login, runs dry or a budget is at risk."
          />
        ) : (
          <ul className="-mx-1 max-h-[26rem] divide-y overflow-y-auto">
            {open.map((alert) => {
              const meta = SEVERITY_META[alert.severity];
              const Icon = ICONS[alert.severity];
              const link = refLink(alert.ref, pools);
              const tracked = pending[alert.id];
              return (
                <li
                  key={alert.id}
                  className={cn("flex gap-3 px-1 py-3 transition-opacity", tracked ? "opacity-60" : null)}
                >
                  <Icon aria-hidden="true" className={cn("mt-0.5 size-4 shrink-0", TONE[meta.tone].text)} />
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <p className="text-sm leading-snug">{alert.message}</p>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground text-xs">
                      <ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>
                      <RefLabel reference={alert.ref} href={link} />
                      <RelativeTime iso={alert.created_at} />
                      <CommandChip command={tracked} />
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="shrink-0"
                    disabled={Boolean(tracked)}
                    onClick={() =>
                      run(
                        "ack_alert",
                        { alert_id: alert.id },
                        { key: alert.id, label: `Acknowledge ${alert.kind.replace("_", " ")}` },
                      )
                    }
                  >
                    Acknowledge
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
