"use client";

import type React from "react";
import { forwardRef, useState } from "react";

import { ChevronRight } from "lucide-react";

import { formatCompactNumber } from "@/lib/format";

export interface BreakdownRow {
  label: string;
  icon?: React.ReactNode;
  value: number | string;
  share: number; // 0 to 100
  color?: string;
  sublabel?: string;
}

export interface BreakdownTab {
  id: string;
  label: string;
  rows: BreakdownRow[];
}

export interface SegmentedTabsProps {
  tabs: Array<{ id: string; label: string }>;
  activeTab: string;
  onTabChange: (id: string) => void;
  className?: string;
}

export const SegmentedTabs: React.FC<SegmentedTabsProps> = ({ tabs, activeTab, onTabChange, className = "" }) => {
  return (
    <div
      role="tablist"
      aria-label="Breakdown view tabs"
      className={`inline-flex items-center gap-1 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-1 dark:bg-[#21262d] ${className}`}
    >
      {tabs.map((tab) => {
        const isActive = tab.id === activeTab;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            type="button"
            onClick={() => onTabChange(tab.id)}
            className={`cursor-pointer select-none rounded-[calc(var(--kit-radius-control,8px)-2px)] px-3 py-1 font-medium text-xs transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1a63d6)] ${
              isActive
                ? "bg-[var(--kit-bg-card,#ffffff)] font-semibold text-[var(--kit-text-primary,#111827)] shadow-sm dark:bg-[#161b22] dark:text-[#f0f6fc]"
                : "text-[var(--kit-text-secondary,#5b6370)] hover:text-[var(--kit-text-primary,#111827)] dark:text-[#8b949e] dark:hover:text-[#f0f6fc]"
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
};

export interface RankedBarListProps {
  rows: BreakdownRow[];
  maxRows?: number;
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

export const RankedBarList: React.FC<RankedBarListProps> = ({ rows, maxRows, className = "" }) => {
  const displayRows = maxRows ? rows.slice(0, maxRows) : rows;

  return (
    <div className={`space-y-3.5 ${className}`}>
      {displayRows.map((row, index) => {
        const color = row.color ?? DEFAULT_CATEGORY_COLORS[index % DEFAULT_CATEGORY_COLORS.length];
        const formattedVal = typeof row.value === "number" ? formatCompactNumber(row.value) : row.value;

        return (
          <div key={row.label} className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <div className="flex items-center gap-2 truncate">
                {row.icon && (
                  <span className="flex shrink-0 items-center text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
                    {row.icon}
                  </span>
                )}
                <span className="truncate font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                  {row.label}
                </span>
                {row.sublabel && (
                  <span className="truncate text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
                    {row.sublabel}
                  </span>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2 tabular-nums">
                <span className="font-semibold text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                  {formattedVal}
                </span>
                <span className="font-semibold text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
                  {Math.round(row.share)}%
                </span>
              </div>
            </div>

            {/* Colored ranked progress bar */}
            <div
              role="progressbar"
              aria-valuenow={row.share}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${row.label} share ${Math.round(row.share)}%`}
              className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]"
            >
              <div
                className="h-full rounded-full transition-all duration-300 ease-out"
                style={{
                  width: `${Math.min(100, Math.max(0, row.share))}%`,
                  backgroundColor: color,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
};

export interface BreakdownCardProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  totalValue?: number | string;
  tabs: BreakdownTab[];
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  maxRows?: number;
  viewAllHref?: string;
  onViewAll?: () => void;
  loading?: boolean;
  empty?: boolean;
  error?: string;
}

export const BreakdownCard = forwardRef<HTMLDivElement, BreakdownCardProps>(
  (
    {
      title,
      totalValue,
      tabs,
      activeTab: controlledActiveTab,
      onTabChange: controlledOnTabChange,
      maxRows = 5,
      viewAllHref,
      onViewAll,
      loading = false,
      empty = false,
      error,
      className = "",
      ...props
    },
    ref,
  ) => {
    const [internalActiveTab, setInternalActiveTab] = useState(tabs[0]?.id ?? "");
    const activeTab = controlledActiveTab ?? internalActiveTab;
    const onTabChange = controlledOnTabChange ?? setInternalActiveTab;

    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="flex items-center justify-between">
            <div className="h-4 w-28 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-7 w-32 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="h-7 w-20 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="space-y-3 pt-2">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="space-y-1.5">
                <div className="flex justify-between">
                  <div className="h-3 w-24 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
                  <div className="h-3 w-12 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
                </div>
                <div className="h-1.5 w-full rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
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
          <h3 className="font-semibold text-sm">{title}</h3>
          <p className="text-[var(--kit-negative-text,#b91c1c)] text-xs dark:text-[#ff7b72]">
            Failed to load breakdown: {error}
          </p>
        </div>
      );
    }

    const currentTab = tabs.find((t) => t.id === activeTab) ?? tabs[0];
    const rows = currentTab?.rows ?? [];

    return (
      <div
        ref={ref}
        className={`space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {/* Header & Tabs */}
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div>
            <h3 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
              {title}
            </h3>
            {totalValue !== undefined && (
              <div className="mt-0.5 font-semibold text-[var(--kit-text-primary,#111827)] text-xl tabular-nums tracking-tight dark:text-[#f0f6fc]">
                {typeof totalValue === "number" ? formatCompactNumber(totalValue) : totalValue}
              </div>
            )}
          </div>

          {tabs.length > 1 && (
            <SegmentedTabs
              tabs={tabs.map((t) => ({ id: t.id, label: t.label }))}
              activeTab={activeTab}
              onTabChange={onTabChange}
            />
          )}
        </div>

        {/* Ranked rows list */}
        {empty || rows.length === 0 ? (
          <div className="py-8 text-center text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
            No breakdown data available.
          </div>
        ) : (
          <RankedBarList rows={rows} maxRows={maxRows} />
        )}

        {/* View all footer link */}
        {(Boolean(viewAllHref) || Boolean(onViewAll)) && rows.length > 0 && (
          <div className="border-[var(--kit-border,#e6e8ec)] border-t pt-2 dark:border-[#30363d]">
            {viewAllHref ? (
              <a
                href={viewAllHref}
                className="inline-flex items-center gap-1 font-medium text-[var(--kit-accent,#1a63d6)] text-xs hover:underline dark:text-[#58a6ff]"
              >
                <span>View all breakdown</span>
                <ChevronRight className="h-3.5 w-3.5" />
              </a>
            ) : (
              <button
                type="button"
                onClick={onViewAll}
                className="inline-flex cursor-pointer items-center gap-1 font-medium text-[var(--kit-accent,#1a63d6)] text-xs hover:underline dark:text-[#58a6ff]"
              >
                <span>View all breakdown</span>
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        )}
      </div>
    );
  },
);

BreakdownCard.displayName = "BreakdownCard";
