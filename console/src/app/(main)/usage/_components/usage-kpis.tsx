import { AlertTriangle, Clock, DollarSign, TrendingUp } from "lucide-react";

import { MetricCard, PlanCard } from "@/components/kit";
import { FORECAST_METHOD_EXPLANATION, isForecastOverBudget } from "@/lib/farm/forecast";
import { fmtUsd } from "@/lib/farm/format";

import type { UsageModel } from "../_lib/usage-model";

export function UsageKpis({
  usage,
  paidAccountsCount,
  idlePaidCount,
}: {
  usage: UsageModel;
  paidAccountsCount: number;
  idlePaidCount: number;
}) {
  const month = usage.month;
  const budget = month?.budget_usd ?? null;
  const spendMtd = month?.spend_usd ?? 0;
  const forecast = month?.forecast_usd ?? 0;
  const overBudget = isForecastOverBudget(forecast, budget);

  return (
    <section aria-label="Usage and cost overview" className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label={`Spend, ${usage.periodLabel}`}
          value={usage.spend}
          format="usd"
          icon={<DollarSign className="size-4" />}
          delta={usage.deltaPct === null ? undefined : { value: usage.deltaPct, goodDirection: "down" }}
          comparison={usage.deltaPct === null ? undefined : "vs previous period"}
        />
        <MetricCard
          label="Month-to-date spend"
          value={fmtUsd(spendMtd, { whole: true })}
          icon={<DollarSign className="size-4" />}
          comparison={budget ? `of ${fmtUsd(budget, { whole: true })} budget` : "no budget set"}
        />
        <MetricCard
          label="Month-end forecast"
          value={fmtUsd(forecast, { whole: true })}
          icon={<TrendingUp className="size-4" />}
          comparison={`Method: ${FORECAST_METHOD_EXPLANATION}`}
        />
        <MetricCard
          label="Idle paid accounts"
          value={idlePaidCount}
          icon={<Clock className="size-4" />}
          comparison={`${paidAccountsCount} paid accounts · 0 successful calls in 14 days`}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        {overBudget ? (
          <div
            data-testid="forecast-flag"
            className="flex items-center gap-1.5 font-medium text-amber-800 text-xs dark:text-amber-400"
          >
            <AlertTriangle className="size-3.5 shrink-0" />
            <span>Forecast exceeds monthly budget!</span>
          </div>
        ) : (
          <span className="text-emerald-700 text-xs dark:text-emerald-400">Within monthly budget</span>
        )}
      </div>

      {budget && budget > 0 ? (
        <PlanCard
          plan="Monthly budget"
          capsUsed={[{ label: "Month-to-date spend", used: spendMtd, total: budget, unit: "$" }]}
        />
      ) : null}
    </section>
  );
}
