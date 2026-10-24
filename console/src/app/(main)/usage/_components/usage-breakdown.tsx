import { BreakdownCard } from "@/components/kit";
import { fmtUsd } from "@/lib/farm/format";

import type { UsageModel, UsageShareRow } from "../_lib/usage-model";

const toRow = (r: UsageShareRow) => ({
  label: r.label,
  value: fmtUsd(r.value),
  share: r.share,
  color: r.color,
  sublabel: r.sublabel,
});

/** Spend split by AI, tool, account and day. Spend by model is not recorded by the Farm, so there is no model tab. */
export function UsageBreakdown({ usage }: { usage: UsageModel }) {
  return (
    <BreakdownCard
      title={`Spend by, ${usage.periodLabel}`}
      totalValue={fmtUsd(usage.spend)}
      tabs={[
        { id: "ai", label: "AI", rows: usage.byAi.map(toRow) },
        { id: "tool", label: "Tools", rows: usage.byTool.map(toRow) },
        {
          id: "account",
          label: "Account (month)",
          rows: usage.byAccount.map(toRow),
        },
        { id: "day", label: "Day", rows: usage.byDay.map(toRow) },
      ]}
      empty={usage.spend === 0}
    />
  );
}
