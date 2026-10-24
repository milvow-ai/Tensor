"use client";

import React, { forwardRef } from "react";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatCompactNumber } from "@/lib/format";

import { DeltaPill, type GoodDirection } from "./DeltaPill";
import { FilterSelect, type FilterSelectOption } from "./FilterSelect";

export interface ChartSeries {
  name: string;
  key: string;
  color: string;
}

export interface ChartDataPoint {
  date: string;
  [seriesKey: string]: number | string;
}

export interface ChartTooltipProps {
  active?: boolean;
  payload?: Array<{
    name: string;
    value: number | string;
    dataKey: string;
    color: string;
    payload: ChartDataPoint;
  }>;
  label?: string;
  metric?: string;
}

export const ChartTooltip: React.FC<ChartTooltipProps> = ({ active, payload, label, metric = "" }) => {
  if (!active || !payload || payload.length === 0) {
    return null;
  }

  const total = payload.reduce((acc, curr) => {
    const val = typeof curr.value === "number" ? curr.value : Number(curr.value) || 0;
    return acc + val;
  }, 0);

  return (
    <div className="z-50 min-w-[180px] rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-3 text-xs shadow-[var(--kit-shadow-popover)] dark:border-[#30363d] dark:bg-[#161b22]">
      <div className="mb-2 flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
        <span className="font-semibold text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">{label}</span>
        <span className="font-semibold text-[var(--kit-text-secondary,#6b7280)] tabular-nums dark:text-[#8b949e]">
          Total: {formatCompactNumber(total)} {metric}
        </span>
      </div>
      <div className="space-y-1.5">
        {payload.map((item) => {
          const itemVal = typeof item.value === "number" ? item.value : Number(item.value) || 0;
          const share = total > 0 ? Math.round((itemVal / total) * 100) : 0;
          return (
            <div key={item.dataKey} className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-1.5 truncate">
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: item.color }}
                  aria-hidden="true"
                />
                <span className="truncate text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
                  {item.name}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-2 tabular-nums">
                <span className="font-semibold text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                  {formatCompactNumber(itemVal)}
                </span>
                <span className="text-[10px] text-[var(--kit-text-muted,#9ca3af)] dark:text-[#6e7681]">({share}%)</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export interface TrendChartCardProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  value?: number | string;
  delta?: { value: number; goodDirection?: GoodDirection };
  series: ChartSeries[];
  data: ChartDataPoint[];
  metric?: string;
  filters?: FilterSelectOption[];
  selectedFilter?: string;
  onFilterChange?: (filter: string) => void;
  loading?: boolean;
  empty?: boolean;
  error?: string;
}

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED_MOTION);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** True when the viewer asked the system for less motion: the chart then draws at once (screenshots stay whole). */
function usePrefersReducedMotion(): boolean {
  return React.useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia(REDUCED_MOTION).matches,
    () => false,
  );
}

export const TrendChartCard = forwardRef<HTMLDivElement, TrendChartCardProps>(
  (
    {
      title,
      value,
      delta,
      series,
      data,
      metric = "",
      filters,
      selectedFilter,
      onFilterChange,
      loading = false,
      empty = false,
      error,
      className = "",
      ...props
    },
    ref,
  ) => {
    const gradientIdPrefix = React.useId().replace(/:/g, "");
    const reducedMotion = usePrefersReducedMotion();

    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="flex items-center justify-between">
            <div className="h-4 w-32 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-8 w-24 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="h-6 w-20 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="h-48 w-full rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
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
            Failed to render chart: {error}
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
        {/* Card Header */}
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
              {title}
            </h3>
            {(value !== undefined || delta) && (
              <div className="mt-1 flex items-center gap-2">
                {value !== undefined && (
                  <span className="font-semibold text-[var(--kit-text-primary,#111827)] text-xl tabular-nums tracking-tight dark:text-[#f0f6fc]">
                    {typeof value === "number" ? formatCompactNumber(value) : value}
                  </span>
                )}
                {delta && <DeltaPill value={delta.value} goodDirection={delta.goodDirection} />}
              </div>
            )}
          </div>

          {filters && filters.length > 0 && selectedFilter && onFilterChange && (
            <FilterSelect
              options={filters}
              value={selectedFilter}
              onChange={onFilterChange}
              aria-label={`${title} filter`}
            />
          )}
        </div>

        {/* Chart View */}
        {empty || data.length === 0 ? (
          <div className="flex h-56 flex-col items-center justify-center rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] border-dashed text-[var(--kit-text-secondary,#6b7280)] text-xs dark:border-[#30363d] dark:text-[#8b949e]">
            <span>No data points for this period.</span>
          </div>
        ) : (
          <div className="h-56 min-h-[224px] w-full min-w-0 pt-2">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  {series.map((s) => (
                    <linearGradient key={s.key} id={`${gradientIdPrefix}-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={s.color} stopOpacity={0.35} />
                      <stop offset="95%" stopColor={s.color} stopOpacity={0.02} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  vertical={false}
                  stroke="var(--kit-border, #e6e8ec)"
                  opacity={0.6}
                />
                <XAxis
                  dataKey="date"
                  tickLine={false}
                  axisLine={false}
                  tick={{
                    fontSize: 11,
                    fill: "var(--kit-text-secondary, #6b7280)",
                    fontFamily: "var(--font-geist-sans), sans-serif",
                    fontWeight: 600,
                  }}
                  className="tabular-nums"
                  dy={6}
                />
                <YAxis
                  tickLine={false}
                  axisLine={false}
                  tick={{
                    fontSize: 11,
                    fill: "var(--kit-text-secondary, #6b7280)",
                    fontFamily: "var(--font-geist-sans), sans-serif",
                    fontWeight: 600,
                  }}
                  className="tabular-nums"
                  tickFormatter={(val: number) => formatCompactNumber(val)}
                  dx={-4}
                />
                <Tooltip
                  content={<ChartTooltip metric={metric} />}
                  cursor={{ stroke: "var(--kit-accent, #1f6feb)", strokeWidth: 1, strokeDasharray: "2 2" }}
                />
                {series.map((s) => (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.name}
                    stroke={s.color}
                    strokeWidth={2}
                    fillOpacity={1}
                    fill={`url(#${gradientIdPrefix}-${s.key})`}
                    stackId="1"
                    isAnimationActive={!reducedMotion}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    );
  },
);

TrendChartCard.displayName = "TrendChartCard";
