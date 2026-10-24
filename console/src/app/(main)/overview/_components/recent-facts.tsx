import Link from "next/link";
import { ArrowUpRight, Brain } from "lucide-react";

import { RelativeTime } from "@/components/farm/time";
import type { FactRow } from "@/lib/farm/types";

export function RecentFacts({ facts }: { facts: FactRow[] }) {
  return (
    <div className="flex flex-col space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22]">
      {/* Header */}
      <div className="flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b pb-3 dark:border-[#30363d]">
        <div className="space-y-0.5">
          <div className="flex items-center gap-1.5">
            <Brain className="size-4 text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]" />
            <h2 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
              Recently learned
            </h2>
          </div>
          <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            Facts extracted and remembered by agents.
          </p>
        </div>
        <Link
          href="/memory"
          className="inline-flex items-center gap-1 font-medium text-[var(--kit-accent,#1a63d6)] text-xs hover:underline dark:text-[#58a6ff]"
        >
          <span>Memory</span>
          <ArrowUpRight className="size-3.5" />
        </Link>
      </div>

      {/* List */}
      <div className="flex-1 divide-y divide-[var(--kit-border,#e6e8ec)] overflow-y-auto dark:divide-[#30363d]">
        {facts.length === 0 ? (
          <div className="py-8 text-center text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            No memories saved yet. Agents store facts here as they run.
          </div>
        ) : (
          facts.slice(0, 5).map((fact) => (
            <div key={fact.id} className="space-y-1 py-2.5 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-semibold text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                  {fact.entity_name}
                </span>
                <span
                  className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                    fact.freshness_state === "fresh"
                      ? "bg-[var(--kit-positive-soft,#dcfce7)] text-[var(--kit-positive-text,#15803d)] dark:bg-[rgba(63,185,80,0.15)] dark:text-[#56d364]"
                      : "bg-[var(--kit-bg-subtle,#f3f4f6)] text-[var(--kit-text-secondary,#5b6370)] dark:bg-[#21262d] dark:text-[#8b949e]"
                  }`}
                >
                  {fact.freshness_state}
                </span>
              </div>
              <p className="line-clamp-2 text-[var(--kit-text-secondary,#5b6370)] text-[11px] leading-relaxed dark:text-[#8b949e]">
                <span className="font-mono text-[10px] text-[var(--kit-text-muted,#9ca3af)] dark:text-[#6e7681]">
                  {fact.attribute}:
                </span>{" "}
                {typeof fact.value === "string" ? fact.value : JSON.stringify(fact.value)}
              </p>
              <div className="flex items-center justify-between text-[10px] text-[var(--kit-text-muted,#9ca3af)] dark:text-[#6e7681]">
                <span>{fact.source_label ?? fact.source_provider_id ?? "agent"}</span>
                <RelativeTime iso={fact.observed_at} />
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
