import { cn } from "cn";
import { Activity, Bell, DollarSign, Server } from "lucide-react";

import { KpiCard } from "@/components/farm/kpi-card";
import { fmtInt, fmtPct, fmtUsd } from "@/lib/farm/format";
import { type summarizeOverview, TONE } from "@/lib/farm/state";

type Summary = ReturnType<typeof summarizeOverview>;

function HealthBar({
  healthy,
  degraded,
  down,
  total,
}: {
  healthy: number;
  degraded: number;
  down: number;
  total: number;
}) {
  if (total === 0) return null;
  const segments = [
    { key: "healthy", count: healthy, className: TONE.ok.bar },
    { key: "degraded", count: degraded, className: TONE.warn.bar },
    { key: "down", count: down, className: TONE.bad.bar },
  ].filter((segment) => segment.count > 0);
  return (
    <div aria-hidden="true" className="mt-0.5 flex h-1.5 gap-0.5 overflow-hidden rounded-full">
      {segments.map((segment) => (
        <div
          key={segment.key}
          className={cn("h-full rounded-full", segment.className)}
          style={{ flexGrow: segment.count }}
        />
      ))}
    </div>
  );
}

export function KpiRow({
  summary,
  daysInMonth,
  elapsedDays,
}: {
  summary: Summary;
  daysInMonth: number;
  elapsedDays: number;
}) {
  const budget = summary.budget;
  const spendShare = budget && budget > 0 ? Math.min(summary.spend / budget, 1) : 0;
  const forecastShare = budget && budget > 0 ? Math.min(summary.forecast / budget, 1) : 0;
  const overBudget = budget !== null && budget > 0 && summary.forecast > budget;
  const forecastRatio = budget && budget > 0 ? summary.forecast / budget : null;

  return (
    <section aria-label="Key numbers" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard label="Pools healthy" icon={Server} value={summary.poolsHealthy} suffix={`/ ${summary.poolsTotal}`}>
        <HealthBar
          healthy={summary.poolsHealthy}
          degraded={summary.poolsDegraded}
          down={summary.poolsDown}
          total={summary.poolsTotal}
        />
        <p className="mt-1.5 text-muted-foreground">
          {summary.poolsDegraded} degraded, {summary.poolsDown} down
        </p>
      </KpiCard>

      <KpiCard
        label="Accounts active"
        icon={Activity}
        value={summary.accountsActive}
        suffix={`/ ${summary.accountsTotal}`}
      >
        <p className="text-muted-foreground">
          {fmtInt(summary.accountsUsable)} routable right now, {fmtInt(summary.accountsTotal - summary.accountsActive)}{" "}
          paused or blocked
        </p>
      </KpiCard>

      <KpiCard
        label="Spend this month"
        icon={DollarSign}
        value={fmtUsd(summary.spend, { whole: true })}
        suffix={budget ? `of ${fmtUsd(budget, { whole: true })} budget` : "no budget set"}
      >
        {budget ? (
          <div>
            <meter
              className="sr-only"
              aria-label="Spend against monthly budget"
              min={0}
              max={budget}
              value={Math.min(summary.spend, budget)}
            />
            <div aria-hidden="true" className="relative mt-0.5 h-1.5 rounded-full bg-muted">
              <div
                className={cn("h-full rounded-full", overBudget ? TONE.warn.bar : TONE.ok.bar)}
                style={{ width: `${spendShare * 100}%` }}
              />
              <div
                title="Forecast for month end"
                className="absolute -top-0.5 h-2.5 w-0.5 rounded-full bg-foreground/70"
                style={{ left: `calc(${forecastShare * 100}% - 1px)` }}
              />
            </div>
            <p className={cn("mt-1.5", overBudget ? TONE.warn.text : "text-muted-foreground")}>
              Forecast {fmtUsd(summary.forecast, { whole: true })} ({fmtPct(forecastRatio)} of budget) from day{" "}
              {Math.floor(elapsedDays)} of {daysInMonth}
            </p>
          </div>
        ) : (
          <p className="text-muted-foreground">Forecast {fmtUsd(summary.forecast, { whole: true })}</p>
        )}
      </KpiCard>

      <KpiCard label="Open alerts" icon={Bell} value={summary.openAlerts}>
        {summary.openAlerts === 0 ? (
          <p className="text-muted-foreground">Nothing needs attention.</p>
        ) : (
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className={cn("size-1.5 rounded-full", TONE.bad.dot)} />
              {summary.criticalAlerts} critical
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden="true" className={cn("size-1.5 rounded-full", TONE.warn.dot)} />
              {summary.warnAlerts} warning
            </span>
          </p>
        )}
      </KpiCard>
    </section>
  );
}
