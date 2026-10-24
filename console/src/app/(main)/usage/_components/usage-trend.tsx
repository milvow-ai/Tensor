import { TrendChartCard } from "@/components/kit";

import type { UsageModel } from "../_lib/usage-model";

export function UsageTrend({ usage }: { usage: UsageModel }) {
  return (
    <TrendChartCard
      title="Spend over time"
      value={usage.spend}
      delta={usage.deltaPct === null ? undefined : { value: usage.deltaPct, goodDirection: "down" }}
      series={usage.series.map((s) => ({ key: s.key, name: s.name, color: s.color }))}
      data={usage.chart.map((row) => ({ ...row, date: String(row.date) }))}
      metric="usd"
      empty={usage.chart.length === 0}
    />
  );
}
