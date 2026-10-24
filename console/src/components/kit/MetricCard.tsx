import type React from "react";
import { forwardRef } from "react";

import { ChevronRight } from "lucide-react";

import { formatValue, type ValueFormatType } from "@/lib/format";

import { DeltaPill } from "./DeltaPill";

export interface MetricCardDelta {
  value: number;
  goodDirection?: "up" | "down";
  neutralBelow?: number;
}

export interface MetricCardProps extends React.HTMLAttributes<HTMLDivElement> {
  label: string;
  value: number | string;
  format?: ValueFormatType;
  delta?: MetricCardDelta;
  icon?: React.ReactNode;
  comparison?: string;
  href?: string;
  locale?: string;
  timeZone?: string;
  loading?: boolean;
  empty?: boolean;
  error?: string;
}

export const MetricCard = forwardRef<HTMLDivElement, MetricCardProps>(
  (
    {
      label,
      value,
      format = "number",
      delta,
      icon,
      comparison,
      href,
      locale = "en-US",
      timeZone = "UTC",
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
          <div className="flex items-center justify-between">
            <div className="h-3.5 w-24 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-6 w-6 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="h-8 w-32 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="flex items-center gap-2 border-[var(--kit-border,#e6e8ec)] border-t pt-1 dark:border-[#30363d]">
            <div className="h-4 w-14 rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-3 w-20 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
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
          <div className="font-semibold text-[var(--kit-negative-text,#b91c1c)] text-xs dark:text-[#ff7b72]">
            {label}
          </div>
          <p className="text-[var(--kit-text-secondary,#6b7280)] text-xs dark:text-[#8b949e]">
            Failed to load metric: {error}
          </p>
        </div>
      );
    }

    let formattedValue: React.ReactNode = "—";
    if (!empty) {
      if (typeof value === "number") {
        formattedValue = formatValue(value, format, locale, timeZone);
      } else {
        formattedValue = value;
      }
    }

    const hasFooter = Boolean(delta) || Boolean(comparison) || Boolean(href);

    const CardContent = (
      <div className="space-y-3">
        {/* Header: Label and Logo / Icon */}
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-medium text-[var(--kit-text-secondary,#6b7280)] text-xs dark:text-[#8b949e]">
            {label}
          </span>
          {icon && (
            <div className="flex h-6 w-6 shrink-0 items-center justify-center text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
              {icon}
            </div>
          )}
        </div>

        {/* Lead with Big Number */}
        <div className="font-semibold text-2xl text-[var(--kit-text-primary,#111827)] tabular-nums tracking-tight sm:text-[28px] dark:text-[#f0f6fc]">
          {formattedValue}
        </div>

        {/* Footer: Delta pill + comparison text + optional drilldown arrow */}
        {hasFooter && (
          <div className="flex items-center justify-between gap-2 border-[var(--kit-border,#e6e8ec)] border-t pt-2 text-xs dark:border-[#30363d]">
            <div className="flex items-center gap-2 truncate">
              {delta && (
                <DeltaPill value={delta.value} goodDirection={delta.goodDirection} neutralBelow={delta.neutralBelow} />
              )}
              {comparison && (
                <span className="truncate text-[11px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
                  {comparison}
                </span>
              )}
            </div>
            {href && (
              <ChevronRight
                aria-hidden="true"
                className="h-4 w-4 shrink-0 text-[var(--kit-text-secondary,#6b7280)] transition-transform group-hover:translate-x-0.5 dark:text-[#8b949e]"
              />
            )}
          </div>
        )}
      </div>
    );

    const baseClasses = `p-5 rounded-[var(--kit-radius-card,12px)] bg-[var(--kit-bg-card,#ffffff)] dark:bg-[#161b22] border border-[var(--kit-border,#e6e8ec)] dark:border-[#30363d] shadow-[var(--kit-shadow-card)] transition-all ${
      href ? "group cursor-pointer hover:border-[var(--kit-accent,#1f6feb)]/50 hover:shadow-md" : ""
    } ${className}`;

    if (href) {
      return (
        <div ref={ref} className={baseClasses} {...props}>
          <a href={href} className="block text-inherit no-underline focus:outline-none">
            {CardContent}
          </a>
        </div>
      );
    }

    return (
      <div ref={ref} className={baseClasses} {...props}>
        {CardContent}
      </div>
    );
  },
);

MetricCard.displayName = "MetricCard";
