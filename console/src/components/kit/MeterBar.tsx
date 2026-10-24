import type React from "react";
import { forwardRef } from "react";

export type MeterBarMode = "category" | "threshold";

export interface MeterBarProps extends React.HTMLAttributes<HTMLDivElement> {
  value: number;
  max: number;
  mode?: MeterBarMode;
  color?: string;
  caption?: string;
  label?: string;
  showValue?: boolean;
  loading?: boolean;
  error?: string;
}

export const MeterBar = forwardRef<HTMLDivElement, MeterBarProps>(
  (
    {
      value,
      max,
      mode = "threshold",
      color,
      caption,
      label,
      showValue = false,
      loading = false,
      error,
      className = "",
      ...props
    },
    ref,
  ) => {
    if (loading) {
      return (
        <div ref={ref} className={`w-full space-y-1.5 ${className}`} {...props}>
          {(label !== undefined || showValue) && (
            <div className="flex items-center justify-between text-xs">
              <div className="h-3 w-20 animate-pulse rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
              <div className="h-3 w-10 animate-pulse rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            </div>
          )}
          <div className="h-2 w-full animate-pulse overflow-hidden rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          {caption && (
            <div className="h-2.5 w-24 animate-pulse rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          )}
        </div>
      );
    }

    if (error) {
      return (
        <div
          ref={ref}
          role="alert"
          className={`flex items-center gap-1.5 text-[var(--kit-negative-text,#b91c1c)] text-xs dark:text-[#ff7b72] ${className}`}
          {...props}
        >
          <span>Error loading meter: {error}</span>
        </div>
      );
    }

    const safeMax = max > 0 ? max : 1;
    const ratio = Math.min(100, Math.max(0, (value / safeMax) * 100));
    const roundedPercent = Math.round(ratio);

    let barColor = color;
    if (!barColor) {
      if (mode === "threshold") {
        if (ratio >= 100) {
          barColor = "var(--kit-negative, #dc2626)";
        } else if (ratio >= 80) {
          barColor = "var(--kit-warning, #d97706)";
        } else {
          barColor = "var(--kit-accent, #1f6feb)";
        }
      } else {
        barColor = "var(--kit-accent, #1f6feb)";
      }
    }

    return (
      <div ref={ref} className={`w-full space-y-1.5 ${className}`} {...props}>
        {(label !== undefined || showValue) && (
          <div className="flex items-center justify-between text-xs">
            {label && (
              <span className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">{label}</span>
            )}
            {showValue && (
              <span className="font-semibold text-[var(--kit-text-secondary,#5b6370)] tabular-nums dark:text-[#8b949e]">
                {roundedPercent}%
              </span>
            )}
          </div>
        )}

        <div
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={max}
          aria-label={label ?? caption ?? `Progress ${roundedPercent}%`}
          className="relative h-2 w-full overflow-hidden rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]"
        >
          <div
            className="h-full rounded-full transition-all duration-300 ease-out"
            style={{
              width: `${ratio}%`,
              backgroundColor: barColor,
            }}
          />
        </div>

        {caption && (
          <p className="text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">{caption}</p>
        )}
      </div>
    );
  },
);

MeterBar.displayName = "MeterBar";
