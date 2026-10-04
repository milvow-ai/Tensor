"use client";

import { ChevronDown } from "lucide-react";

import { useLazy } from "@/components/farm/use-lazy";
import { STRATEGY_LABELS } from "@/lib/farm/command-meta";
import { POOL_STRATEGIES, type PoolStrategy } from "@/lib/farm/types";

const loadSelect = () => import("./strategy-select").then((module) => module.StrategySelect);

function labelFor(current: string | null): string {
  const known = POOL_STRATEGIES.find((strategy) => strategy === current);
  return known ? STRATEGY_LABELS[known satisfies PoolStrategy].label : "Default";
}

/** Pool strategy picker. The select itself loads after first paint (it pulls in the popover machinery). */
export function StrategySelectLazy({ providerId, current }: { providerId: string; current: string | null }) {
  const Select = useLazy(loadSelect);
  if (Select) return <Select providerId={providerId} current={current} />;
  return (
    <div className="flex flex-col gap-1.5" aria-busy="true">
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground text-xs">Strategy</span>
        <span className="flex h-7 w-44 items-center justify-between rounded-lg border px-2.5 text-sm">
          {labelFor(current)}
          <ChevronDown aria-hidden="true" className="size-4 text-muted-foreground" />
        </span>
      </div>
      <p className="h-4" />
    </div>
  );
}
