import Link from "next/link";

import { cn } from "cn";
import { ArrowUpRight } from "lucide-react";

import { SectionTitle } from "@/components/farm/section-title";
import { DotList, StateSwatch, ToneBadge } from "@/components/farm/status";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { fmtCompact } from "@/lib/farm/format";
import { HEALTH_META, poolHref, STATE_META, STATE_ORDER } from "@/lib/farm/state";
import type { EffectiveState, PoolOverviewRow } from "@/lib/farm/types";

function Legend({ states }: { states: EffectiveState[] }) {
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground text-xs">
      {states.map((state) => (
        <li key={state} className="flex items-center gap-1.5">
          <StateSwatch state={state} size="sm" />
          {STATE_META[state].label}
        </li>
      ))}
    </ul>
  );
}

export function PoolTile({ pool }: { pool: PoolOverviewRow }) {
  const health = HEALTH_META[pool.health];
  return (
    <Link
      href={poolHref(pool)}
      data-testid="pool-tile"
      className="group block rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <Card size="sm" className="h-full gap-3 px-3.5 transition-colors group-hover:bg-muted/40">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="truncate font-medium text-sm">{pool.provider_name}</span>
              <ArrowUpRight
                aria-hidden="true"
                className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
              />
            </div>
            <div className="mt-0.5 text-muted-foreground text-xs">
              {pool.kind === "ai" ? "AI pool" : "Tool pool"} · <span className="font-mono">{pool.executor}</span>
            </div>
          </div>
          <ToneBadge tone={health.tone}>{health.label}</ToneBadge>
        </div>
        {pool.account_dots.length > 0 ? (
          <DotList dots={pool.account_dots} label={`${pool.provider_name} accounts`} />
        ) : (
          <span className="text-muted-foreground text-xs">No accounts yet</span>
        )}
        <dl className="mt-auto grid grid-cols-3 gap-2 border-t pt-2.5 text-xs">
          <div>
            <dt className="text-muted-foreground">Usable</dt>
            <dd className="font-medium tabular-nums">
              {pool.accounts_usable}/{pool.accounts_total}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Calls left</dt>
            <dd
              className={cn(
                "font-medium tabular-nums",
                pool.remaining_calls === 0 && !pool.unlimited ? "text-red-700 dark:text-red-400" : null,
              )}
            >
              {pool.unlimited ? "Unlimited" : fmtCompact(pool.remaining_calls)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Strategy</dt>
            <dd className="truncate font-medium">{pool.default_strategy ?? "default"}</dd>
          </div>
        </dl>
      </Card>
    </Link>
  );
}

export function PoolHealthGrid({ pools }: { pools: PoolOverviewRow[] }) {
  const present = new Set(pools.flatMap((pool) => pool.account_dots.map((dot) => dot.state)));
  const legend = STATE_ORDER.filter((state) => present.has(state));
  return (
    <Card className="gap-3">
      <CardHeader>
        <SectionTitle>Pool health</SectionTitle>
        <CardDescription>
          One tile per pool. Each dot is an account, colored by what the router can do with it.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <Legend states={legend} />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
          {pools.map((pool) => (
            <PoolTile key={pool.provider_id} pool={pool} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
