"use client";

import { AlertTriangle, Clock, CreditCard, DollarSign, TrendingUp } from "lucide-react";

import { KpiCard } from "@/components/farm/kpi-card";
import { ToneBadge } from "@/components/farm/status";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { FORECAST_METHOD_EXPLANATION, isForecastOverBudget } from "@/lib/farm/forecast";
import { fmtPct, fmtUsd } from "@/lib/farm/format";
import type { SpendMonthRow } from "@/lib/farm/types";

function getProgressColor(ratio: number): string {
  if (ratio >= 1) return "var(--color-destructive)";
  if (ratio >= 0.8) return "var(--chart-4)";
  return "var(--primary)";
}

export function BillingKpiRow({
  totalSpendRow,
  paidAccountsCount,
  idlePaidCount,
}: {
  totalSpendRow: SpendMonthRow;
  paidAccountsCount: number;
  idlePaidCount: number;
}) {
  const budget = totalSpendRow.budget_usd;
  const spend = totalSpendRow.spend_usd;
  const forecast = totalSpendRow.forecast_usd;
  const elapsedDays = totalSpendRow.elapsed_days || 1;
  const daysInMonth = totalSpendRow.days_in_month || 30;

  const spendRatio = budget && budget > 0 ? spend / budget : 0;
  const overBudget = isForecastOverBudget(forecast, budget);

  return (
    <TooltipProvider delayDuration={150}>
      <section aria-label="Billing overview numbers" className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {/* KPI 1: Spend MTD vs Global Budget with 50/80/100 % markers */}
        <KpiCard
          label="Month-to-date spend"
          icon={DollarSign}
          value={fmtUsd(spend, { whole: true })}
          suffix={budget ? `of ${fmtUsd(budget, { whole: true })} budget` : "no budget set"}
        >
          {budget && budget > 0 ? (
            <div>
              <meter
                className="sr-only"
                aria-label="Spend progress against budget"
                min={0}
                max={budget}
                value={Math.min(spend, budget)}
              />
              <div aria-hidden="true" className="relative mt-2 h-2.5 w-full rounded-full bg-muted">
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${Math.min(spendRatio * 100, 100)}%`,
                    backgroundColor: getProgressColor(spendRatio),
                  }}
                />
                {/* 50% marker */}
                <div className="absolute top-0 bottom-0 left-1/2 w-0.5 bg-background shadow-xs" title="50% marker" />
                {/* 80% marker */}
                <div className="absolute top-0 bottom-0 left-[80%] w-0.5 bg-background shadow-xs" title="80% marker" />
                {/* 100% marker */}
                <div
                  className="absolute top-0 bottom-0 left-[100%] -ml-0.5 w-0.5 bg-background shadow-xs"
                  title="100% marker"
                />
              </div>
              <div
                aria-hidden="true"
                className="mt-1 flex justify-between text-[10px] text-muted-foreground tabular-nums"
              >
                <span>0%</span>
                <span className="pl-4">50%</span>
                <span>80%</span>
                <span>100%</span>
              </div>
            </div>
          ) : (
            <p className="text-muted-foreground text-xs">No global budget cap set.</p>
          )}
        </KpiCard>

        {/* KPI 2: Forecast to month end with hover method and budget flag */}
        <KpiCard
          label="Month-end forecast"
          icon={TrendingUp}
          value={fmtUsd(forecast, { whole: true })}
          suffix={budget && budget > 0 ? `(${fmtPct(forecast / budget)})` : undefined}
        >
          <div className="flex flex-col gap-1.5">
            <Tooltip>
              <TooltipTrigger asChild>
                <div className="inline-flex cursor-help items-center gap-1 text-muted-foreground text-xs hover:text-foreground">
                  <span>Method: {FORECAST_METHOD_EXPLANATION}</span>
                </div>
              </TooltipTrigger>
              <TooltipContent side="top" className="max-w-xs text-xs">
                <p className="font-semibold">Forecast formula:</p>
                <p className="mt-1">
                  Spend ({fmtUsd(spend)}) ÷ elapsed days ({elapsedDays.toFixed(1)}) × days in month ({daysInMonth}) ={" "}
                  {fmtUsd(forecast)}
                </p>
              </TooltipContent>
            </Tooltip>

            {overBudget ? (
              <div
                data-testid="forecast-flag"
                className="mt-0.5 flex items-center gap-1.5 font-medium text-amber-800 text-xs dark:text-amber-400"
              >
                <AlertTriangle className="size-3.5 shrink-0" />
                <span>Forecast exceeds monthly budget!</span>
              </div>
            ) : (
              <span className="text-emerald-700 text-xs dark:text-emerald-400">Within monthly budget</span>
            )}
          </div>
        </KpiCard>

        {/* KPI 3: Paid accounts count */}
        <KpiCard label="Paid accounts" icon={CreditCard} value={paidAccountsCount} suffix="accounts">
          <p className="text-muted-foreground text-xs">Active paid provider subscriptions across tools & AI</p>
        </KpiCard>

        {/* KPI 4: Idle paid accounts */}
        <KpiCard label="Idle paid accounts" icon={Clock} value={idlePaidCount} suffix="with zero calls">
          <div className="flex items-center gap-2">
            {idlePaidCount > 0 ? (
              <ToneBadge tone="warn">Needs review (14d idle)</ToneBadge>
            ) : (
              <ToneBadge tone="ok">All active</ToneBadge>
            )}
            <span className="text-muted-foreground text-xs">0 successful calls in 14 days</span>
          </div>
        </KpiCard>
      </section>
    </TooltipProvider>
  );
}
