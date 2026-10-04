import Link from "next/link";

import { ArrowUpRight } from "lucide-react";

import { DotList, ToneBadge } from "@/components/farm/status";
import { Card } from "@/components/ui/card";
import { STRATEGY_LABELS } from "@/lib/farm/command-meta";
import { fmtCompact, fmtPlanUsd } from "@/lib/farm/format";
import { HEALTH_META, poolHref } from "@/lib/farm/state";
import type { PoolOverviewRow, PoolStrategy } from "@/lib/farm/types";

function breakdown(pool: PoolOverviewRow): string[] {
  const parts = [
    { count: pool.accounts_usable, text: "usable" },
    { count: pool.accounts_paused, text: "paused" },
    { count: pool.accounts_exhausted, text: "exhausted" },
    { count: pool.accounts_needs_login, text: "need login" },
    { count: pool.accounts_open_circuit, text: "circuit open" },
    { count: pool.accounts_cooldown, text: "cooling down" },
  ];
  return parts.filter((part) => part.count > 0).map((part) => `${part.count} ${part.text}`);
}

export function PoolCard({ pool }: { pool: PoolOverviewRow }) {
  const health = HEALTH_META[pool.health];
  const strategy = pool.default_strategy ? STRATEGY_LABELS[pool.default_strategy as PoolStrategy]?.label : undefined;
  const lines = breakdown(pool);
  return (
    <Link
      href={poolHref(pool)}
      data-testid="pool-card"
      className="group block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <Card className="h-full gap-4 px-4 transition-colors group-hover:bg-muted/40">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <h2 className="truncate font-semibold text-base tracking-tight">{pool.provider_name}</h2>
              <ArrowUpRight
                aria-hidden="true"
                className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
              />
            </div>
            <p className="mt-0.5 text-muted-foreground text-xs">
              Executor <span className="font-mono">{pool.executor}</span> · {pool.accounts_total}{" "}
              {pool.accounts_total === 1 ? "account" : "accounts"}
            </p>
          </div>
          <ToneBadge tone={health.tone}>{health.label}</ToneBadge>
        </div>

        <DotList dots={pool.account_dots} label={`${pool.provider_name} accounts`} />
        <p className="min-h-4 text-muted-foreground text-xs">
          {lines.length > 0 ? lines.join(" · ") : "No accounts yet"}
        </p>

        <dl className="mt-auto grid grid-cols-3 gap-3 border-t pt-3 text-xs">
          <div>
            <dt className="text-muted-foreground">Calls left</dt>
            <dd className="font-medium text-sm tabular-nums">
              {pool.unlimited ? "Unlimited" : fmtCompact(pool.remaining_calls)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Strategy</dt>
            <dd className="truncate font-medium text-sm">{strategy ?? pool.default_strategy ?? "Default"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Plans</dt>
            <dd className="font-medium text-sm tabular-nums">
              {pool.monthly_plan_usd > 0 ? `${fmtPlanUsd(pool.monthly_plan_usd)}/mo` : "Free"}
            </dd>
          </div>
        </dl>
      </Card>
    </Link>
  );
}
