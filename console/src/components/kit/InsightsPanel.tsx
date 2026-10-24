import type React from "react";
import { forwardRef } from "react";

import { formatCompactNumber } from "@/lib/format";

import { DeltaPill, type GoodDirection } from "./DeltaPill";

export interface MetricRowData {
  label: string;
  value: string | number;
  average?: string | number;
  change?: {
    value: number;
    goodDirection?: GoodDirection;
  };
}

export interface MetricRowProps {
  row: MetricRowData;
  className?: string;
}

export const MetricRow: React.FC<MetricRowProps> = ({ row, className = "" }) => {
  const formattedVal = typeof row.value === "number" ? formatCompactNumber(row.value) : row.value;
  const formattedAvg = typeof row.average === "number" ? formatCompactNumber(row.average) : row.average;

  return (
    <div
      className={`flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b py-2 text-xs last:border-0 dark:border-[#30363d] ${className}`}
    >
      <div className="min-w-0 space-y-0.5">
        <div className="truncate font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
          {row.label}
        </div>
        {row.average !== undefined && (
          <div className="text-[11px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
            Avg: <span className="font-semibold tabular-nums">{formattedAvg}</span>
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2.5">
        <span className="font-semibold text-[var(--kit-text-primary,#111827)] tabular-nums dark:text-[#f0f6fc]">
          {formattedVal}
        </span>
        {row.change && <DeltaPill value={row.change.value} goodDirection={row.change.goodDirection} />}
      </div>
    </div>
  );
};

export interface SplitBarSegment {
  label: string;
  share: number; // percentage (0 to 100)
  delta?: {
    value: number;
    goodDirection?: GoodDirection;
  };
  color?: string;
  subtext?: string;
}

export interface SplitBarProps {
  split: SplitBarSegment[];
  className?: string;
}

const DEFAULT_CATEGORY_COLORS = [
  "var(--kit-category-1, #2563eb)",
  "var(--kit-category-2, #0284c7)",
  "var(--kit-category-3, #9333ea)",
  "var(--kit-category-4, #ea580c)",
  "var(--kit-category-5, #16a34a)",
  "var(--kit-category-6, #d97706)",
];

export const SplitBar: React.FC<SplitBarProps> = ({ split, className = "" }) => {
  return (
    <div className={`space-y-2.5 ${className}`}>
      {/* Multi-segment continuous horizontal bar */}
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]">
        {split.map((segment, idx) => {
          const color = segment.color ?? DEFAULT_CATEGORY_COLORS[idx % DEFAULT_CATEGORY_COLORS.length];
          return (
            <div
              key={segment.label}
              title={`${segment.label}: ${Math.round(segment.share)}%`}
              style={{
                width: `${Math.min(100, Math.max(0, segment.share))}%`,
                backgroundColor: color,
              }}
              className="h-full transition-all duration-300 first:rounded-l-full last:rounded-r-full"
            />
          );
        })}
      </div>

      {/* Legend below: "Claude 18% · Codex 6% · DeepSeek 76%" */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs">
        {split.map((segment, idx) => {
          const color = segment.color ?? DEFAULT_CATEGORY_COLORS[idx % DEFAULT_CATEGORY_COLORS.length];
          return (
            <div key={segment.label} className="flex items-center gap-1.5">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} aria-hidden="true" />
              <span className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                {segment.label}
              </span>
              <span className="font-semibold text-[var(--kit-text-secondary,#6b7280)] tabular-nums dark:text-[#8b949e]">
                {Math.round(segment.share)}%
              </span>
              {segment.delta && <DeltaPill value={segment.delta.value} goodDirection={segment.delta.goodDirection} />}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export interface InsightsPanelHeader {
  title: string;
  meta?: string;
  icon?: React.ReactNode;
}

export interface InsightsPanelProps extends React.HTMLAttributes<HTMLDivElement> {
  header: InsightsPanelHeader;
  rows?: MetricRowData[];
  split?: SplitBarSegment[];
  splitTitle?: string;
  loading?: boolean;
  empty?: boolean;
  error?: string;
}

export const InsightsPanel = forwardRef<HTMLDivElement, InsightsPanelProps>(
  (
    {
      header,
      rows = [],
      split,
      splitTitle = "Resource Share Split",
      loading = false,
      empty = false,
      error,
      className = "",
      ...props
    },
    ref,
  ) => {
    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="flex items-center gap-2">
            <div className="h-5 w-5 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-4 w-32 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <div
                key={i}
                className="flex justify-between border-[var(--kit-border,#e6e8ec)] border-b py-2 dark:border-[#30363d]"
              >
                <div className="h-3 w-24 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
                <div className="h-3 w-16 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
              </div>
            ))}
          </div>
        </div>
      );
    }

    if (error) {
      return (
        <div
          ref={ref}
          role="alert"
          className={`space-y-2 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-negative,#dc2626)]/30 bg-[var(--kit-bg-card,#ffffff)] p-5 text-[var(--kit-text-primary,#111827)] dark:bg-[#161b22] dark:text-[#f0f6fc] ${className}`}
          {...props}
        >
          <h3 className="font-semibold text-sm">{header.title}</h3>
          <p className="text-[var(--kit-negative-text,#b91c1c)] text-xs dark:text-[#ff7b72]">
            Failed to load insights: {error}
          </p>
        </div>
      );
    }

    return (
      <div
        ref={ref}
        className={`space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
          <div className="flex items-center gap-2">
            {header.icon && <span className="text-[var(--kit-accent,#1f6feb)] dark:text-[#58a6ff]">{header.icon}</span>}
            <div>
              <h3 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
                {header.title}
              </h3>
              {header.meta && (
                <p className="text-[11px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
                  {header.meta}
                </p>
              )}
            </div>
          </div>
        </div>

        {empty || (rows.length === 0 && (!split || split.length === 0)) ? (
          <div className="py-6 text-center text-[var(--kit-text-secondary,#6b7280)] text-xs dark:text-[#8b949e]">
            No insights available for this period.
          </div>
        ) : (
          <div className="space-y-4">
            {/* Metric rows */}
            {rows.length > 0 && (
              <div>
                {rows.map((row) => (
                  <MetricRow key={row.label} row={row} />
                ))}
              </div>
            )}

            {/* Split bar section */}
            {split && split.length > 0 && (
              <div className="space-y-2 border-[var(--kit-border,#e6e8ec)] border-t pt-2 dark:border-[#30363d]">
                {splitTitle && (
                  <h4 className="font-semibold text-[var(--kit-text-secondary,#6b7280)] text-xs uppercase tracking-wider dark:text-[#8b949e]">
                    {splitTitle}
                  </h4>
                )}
                <SplitBar split={split} />
              </div>
            )}
          </div>
        )}
      </div>
    );
  },
);

InsightsPanel.displayName = "InsightsPanel";
