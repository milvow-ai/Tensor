"use client";

import { forwardRef } from "react";

import { Calendar } from "lucide-react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export interface PeriodOption {
  value: string;
  label: string;
}

export const DEFAULT_PERIOD_OPTIONS: PeriodOption[] = [
  { value: "7d", label: "Last 7 days" },
  { value: "14d", label: "Last 14 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "this-month", label: "This month" },
  { value: "all", label: "All time" },
];

export interface PeriodSelectProps {
  value: string;
  onChange: (value: string) => void;
  options?: PeriodOption[];
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  id?: string;
}

export const PeriodSelect = forwardRef<HTMLButtonElement, PeriodSelectProps>(
  (
    { value, onChange, options = DEFAULT_PERIOD_OPTIONS, disabled = false, loading = false, className = "", id },
    ref,
  ) => {
    if (loading) {
      return (
        <div
          className={`h-9 w-36 animate-pulse rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:bg-[#21262d] ${className}`}
        />
      );
    }

    return (
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          ref={ref}
          id={id}
          aria-label="Select time period"
          className={`h-9 cursor-pointer gap-2 rounded-[var(--kit-radius-control,8px)] border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] px-3 font-medium text-[var(--kit-text-primary,#111827)] text-xs transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1f6feb)] dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#f0f6fc] dark:hover:bg-[#21262d] ${className}`}
        >
          <Calendar className="h-3.5 w-3.5 shrink-0 text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]" />
          <SelectValue placeholder="Select period" />
        </SelectTrigger>
        <SelectContent className="z-50 rounded-[var(--kit-radius-control,8px)] border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] shadow-[var(--kit-shadow-popover)] dark:border-[#30363d] dark:bg-[#161b22]">
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              className="cursor-pointer rounded-[calc(var(--kit-radius-control,8px)-2px)] py-1.5 text-[var(--kit-text-primary,#111827)] text-xs focus:bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] focus:text-[var(--kit-accent,#1f6feb)] dark:text-[#f0f6fc] dark:focus:text-[#58a6ff]"
            >
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  },
);

PeriodSelect.displayName = "PeriodSelect";
