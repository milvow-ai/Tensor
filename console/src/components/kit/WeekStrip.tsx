import type React from "react";
import { forwardRef } from "react";

import { DayChip } from "./DayChip";

export interface WeekStripDay {
  id: string;
  date?: Date | string;
  label: string;
  dayNumber: number | string;
  hasItems?: boolean;
  badgeCount?: number;
  disabled?: boolean;
}

export interface WeekStripProps extends React.HTMLAttributes<HTMLDivElement> {
  days: WeekStripDay[];
  selectedDayId?: string;
  onSelectDay?: (dayId: string) => void;
  loading?: boolean;
  empty?: boolean;
  title?: string;
  action?: React.ReactNode;
}

export const WeekStrip = forwardRef<HTMLDivElement, WeekStripProps>(
  (
    { days, selectedDayId, onSelectDay, loading = false, empty = false, title, action, className = "", ...props },
    ref,
  ) => {
    if (loading) {
      return (
        <div
          ref={ref}
          className={`space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-4 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          {title && (
            <div className="h-4 w-32 animate-pulse rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          )}
          <div className="flex items-center gap-2 overflow-x-auto pb-1">
            {[1, 2, 3, 4, 5, 6, 7].map((i) => (
              <DayChip key={i} label="..." dayNumber=".." loading />
            ))}
          </div>
        </div>
      );
    }

    if (empty || days.length === 0) {
      return (
        <div
          ref={ref}
          className={`rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-6 text-center text-[var(--kit-text-secondary,#6b7280)] dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#8b949e] ${className}`}
          {...props}
        >
          <p className="text-sm">No schedule days available.</p>
        </div>
      );
    }

    return (
      <div
        ref={ref}
        className={`space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-4 shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {(title !== undefined || action !== undefined) && (
          <div className="flex items-center justify-between">
            {title && (
              <h3 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
                {title}
              </h3>
            )}
            {action && <div>{action}</div>}
          </div>
        )}

        <div
          role="radiogroup"
          aria-label={title ?? "Week schedule"}
          className="scrollbar-none flex items-center gap-2 overflow-x-auto pb-1"
        >
          {days.map((day) => {
            const isSelected = day.id === selectedDayId;
            return (
              <DayChip
                key={day.id}
                label={day.label}
                dayNumber={day.dayNumber}
                isSelected={isSelected}
                hasItems={day.hasItems}
                badgeCount={day.badgeCount}
                disabled={day.disabled}
                onClick={() => onSelectDay?.(day.id)}
              />
            );
          })}
        </div>
      </div>
    );
  },
);

WeekStrip.displayName = "WeekStrip";
