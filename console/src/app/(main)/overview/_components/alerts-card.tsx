"use client";

import { useState } from "react";
import Link from "next/link";
import { cn } from "cn";
import { CheckCheck, CircleAlert, Info, OctagonAlert } from "lucide-react";

import { CommandChip } from "@/components/farm/command-chip";
import { EmptyState } from "@/components/farm/states";
import { ToneBadge } from "@/components/farm/status";
import { RelativeTime } from "@/components/farm/time";
import { useCommands } from "@/components/farm/use-command";
import { Button } from "@/components/ui/button";
import { poolHref, SEVERITY_META, TONE } from "@/lib/farm/state";
import type { AlertRow, AlertSeverity, PoolOverviewRow } from "@/lib/farm/types";

const ICONS: Record<AlertSeverity, typeof Info> = {
  critical: OctagonAlert,
  warn: CircleAlert,
  info: Info,
};

function refLink(
  ref: string | null,
  pools: Pick<PoolOverviewRow, "provider_id" | "kind">[],
): string | null {
  if (!ref) return null;
  const pool = pools.find(
    (candidate) => ref === candidate.provider_id || ref.startsWith(`${candidate.provider_id}-`),
  );
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
  const [showOlder, setShowOlder] = useState(false);

  const now = Date.now();
  const TWENTY_FOUR_HOURS = 24 * 60 * 60 * 1000;

  // 1. Show only open alerts
  const open = alerts.filter((alert) => alert.acked_at === null);

  // 2. Newest first
  const sorted = [...open].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  // 3. Last 24 h by default
  const recent = sorted.filter(
    (alert) => now - new Date(alert.created_at).getTime() <= TWENTY_FOUR_HOURS,
  );
  const older = sorted.filter(
    (alert) => now - new Date(alert.created_at).getTime() > TWENTY_FOUR_HOURS,
  );

  let displayed = sorted;
  if (!showOlder) {
    displayed = recent.length > 0 ? recent : sorted.slice(0, 5);
  }

  return (
    <section
      id="alerts"
      data-testid="alerts-card"
      aria-label="Alerts"
      className="flex flex-col space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22]"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b pb-3 dark:border-[#30363d]">
        <div className="space-y-0.5">
          <h2 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
            Alerts
          </h2>
          <p className="text-[var(--kit-text-secondary,#6b7280)] text-xs dark:text-[#8b949e]">
            {showOlder
              ? "All open alerts, newest first."
              : "Open alerts from the last 24 hours, newest first."}
          </p>
        </div>
        <span
          data-testid="alerts-open"
          className="rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] px-2.5 py-0.5 font-semibold text-[var(--kit-text-secondary,#6b7280)] text-xs tabular-nums dark:bg-[#21262d] dark:text-[#8b949e]"
        >
          {open.length} open
        </span>
      </div>

      {/* Content */}
      <div className="flex-1">
        {open.length === 0 ? (
          <EmptyState
            icon={CheckCheck}
            title="All clear"
            description="No open alerts. The Farm raises one when an account needs login, runs dry or a budget is at risk."
          />
        ) : (
          <ul className="-mx-1 max-h-[26rem] divide-y divide-[var(--kit-border,#e6e8ec)] overflow-y-auto dark:divide-[#30363d]">
            {displayed.map((alert) => {
              const meta = SEVERITY_META[alert.severity];
              const Icon = ICONS[alert.severity];
              const link = refLink(alert.ref, pools);
              const tracked = pending[alert.id];
              return (
                <li
                  key={alert.id}
                  className={cn(
                    "flex items-start gap-3 px-1 py-3 transition-opacity",
                    tracked ? "opacity-60" : null,
                  )}
                >
                  <Icon
                    aria-hidden="true"
                    className={cn("mt-0.5 size-4 shrink-0", TONE[meta.tone].text)}
                  />
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <p className="font-medium text-[var(--kit-text-primary,#111827)] text-xs leading-snug dark:text-[#f0f6fc]">
                      {alert.message}
                    </p>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[var(--kit-text-secondary,#6b7280)] text-[11px] dark:text-[#8b949e]">
                      <ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>
                      <RefLabel reference={alert.ref} href={link} />
                      <RelativeTime iso={alert.created_at} />
                      <CommandChip command={tracked} />
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="shrink-0 text-xs"
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
      </div>

      {/* Show older toggle */}
      {older.length > 0 && (
        <div className="border-[var(--kit-border,#e6e8ec)] border-t pt-2.5 text-center dark:border-[#30363d]">
          <button
            type="button"
            onClick={() => setShowOlder(!showOlder)}
            className="cursor-pointer font-medium text-[var(--kit-accent,#1f6feb)] text-xs hover:underline dark:text-[#58a6ff]"
          >
            {showOlder ? "Hide older alerts" : `Show older (${older.length})`}
          </button>
        </div>
      )}
    </section>
  );
}
