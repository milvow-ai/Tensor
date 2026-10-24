import type React from "react";
import { forwardRef } from "react";

export type DayChipSize = "sm" | "md";

export interface DayChipProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  dayNumber: number | string;
  isSelected?: boolean;
  hasItems?: boolean;
  badgeCount?: number;
  loading?: boolean;
  size?: DayChipSize;
}

export const DayChip = forwardRef<HTMLButtonElement, DayChipProps>(
  (
    {
      label,
      dayNumber,
      isSelected = false,
      hasItems = false,
      badgeCount,
      loading = false,
      size = "md",
      className = "",
      disabled,
      ...props
    },
    ref,
  ) => {
    const sizeClasses = size === "sm" ? "h-14 min-w-[56px] px-2 py-1.5" : "h-16 min-w-[56px] px-2 py-2";
    const numberSizeClasses = size === "sm" ? "text-sm font-semibold" : "text-base font-bold";

    if (loading) {
      return (
        <div
          className={`flex ${sizeClasses} animate-pulse flex-col items-center justify-center rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        >
          <div className="mb-2 h-2.5 w-6 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="h-4 w-5 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
        </div>
      );
    }

    const selectedClasses = isSelected
      ? "bg-[var(--kit-accent,#1a63d6)] text-white border-[var(--kit-accent,#1a63d6)] shadow-sm font-semibold"
      : "bg-[var(--kit-bg-card,#ffffff)] dark:bg-[#161b22] text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc] border-[var(--kit-border,#e6e8ec)] dark:border-[#30363d] hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:hover:bg-[#21262d]";

    return (
      <button
        ref={ref}
        type="button"
        disabled={disabled}
        aria-pressed={isSelected}
        aria-label={`${label} ${dayNumber}${hasItems ? ", has scheduled tasks" : ""}`}
        className={`relative flex ${sizeClasses} cursor-pointer select-none flex-col items-center justify-center rounded-[var(--kit-radius-control,8px)] border transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1a63d6)] disabled:cursor-not-allowed disabled:opacity-40 ${selectedClasses} ${className}`}
        {...props}
      >
        <span
          className={`block max-w-full truncate text-[11px] font-medium leading-tight ${
            isSelected ? "text-blue-100" : "text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]"
          }`}
        >
          {label}
        </span>
        <span className={`mt-0.5 ${numberSizeClasses} tabular-nums`}>{dayNumber}</span>

        {hasItems && !isSelected && (
          <span
            aria-hidden="true"
            className="absolute bottom-1.5 h-1.5 w-1.5 rounded-full bg-[var(--kit-accent,#1a63d6)] dark:bg-[#388bfd]"
          />
        )}

        {typeof badgeCount === "number" && badgeCount > 0 && (
          <span
            aria-hidden="true"
            className={`absolute -top-1.5 -right-1.5 flex h-4 min-w-[16px] items-center justify-center rounded-full px-1 font-bold text-[10px] ${
              isSelected ? "bg-white text-[var(--kit-accent,#1a63d6)]" : "bg-[var(--kit-accent,#1a63d6)] text-white"
            }`}
          >
            {badgeCount}
          </span>
        )}
      </button>
    );
  },
);

DayChip.displayName = "DayChip";
