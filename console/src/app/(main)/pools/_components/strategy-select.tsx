"use client";

import { useState } from "react";

import { CommandChip } from "@/components/farm/command-chip";
import { useSharedCommands } from "@/components/farm/commands-provider";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { STRATEGY_LABELS } from "@/lib/farm/command-meta";
import { POOL_STRATEGIES, type PoolStrategy } from "@/lib/farm/types";

import { isInFlight } from "../_lib/account-model";

function isStrategy(value: string | null): value is PoolStrategy {
  return POOL_STRATEGIES.some((strategy) => strategy === value);
}

/** Pool strategy picker. Shows the choice at once while the Farm applies it; falls back if the Farm refuses. */
export function StrategySelect({ providerId, current }: { providerId: string; current: string | null }) {
  const { pending, run } = useSharedCommands();
  const key = `strategy:${providerId}`;
  const tracked = pending[key];
  const [intent, setIntent] = useState<PoolStrategy | null>(null);
  const showIntent = intent !== null && (isInFlight(tracked) || tracked?.phase === "done");
  const value = showIntent ? intent : current;
  const hint = isStrategy(value) ? STRATEGY_LABELS[value].hint : "The pool uses the Farm's default order.";

  return (
    <div className="flex flex-col gap-1.5" data-testid="strategy">
      <div className="flex items-center gap-2">
        <span id={`${key}-label`} className="text-muted-foreground text-xs">
          Strategy
        </span>
        <Select
          value={value ?? undefined}
          disabled={isInFlight(tracked)}
          onValueChange={(next) => {
            if (!isStrategy(next) || next === current) return;
            setIntent(next);
            void run(
              "set_strategy",
              { provider_id: providerId, strategy: next },
              {
                key,
                label: `Set ${providerId} strategy to ${STRATEGY_LABELS[next].label}`,
                onRejected: () => setIntent(null),
              },
            );
          }}
        >
          <SelectTrigger size="sm" className="w-44" aria-labelledby={`${key}-label`}>
            <SelectValue placeholder="Default" />
          </SelectTrigger>
          <SelectContent align="end">
            {POOL_STRATEGIES.map((strategy) => (
              <SelectItem key={strategy} value={strategy}>
                {STRATEGY_LABELS[strategy].label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <CommandChip command={tracked} />
      </div>
      <p className="max-w-xs text-muted-foreground text-xs">{hint}</p>
    </div>
  );
}
