"use client";

import type React from "react";
import { forwardRef } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";

export interface FilterSelectOption {
  value: string;
  label: string;
  icon?: React.ReactNode;
}

export interface FilterSelectProps {
  options: FilterSelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  id?: string;
  "aria-label"?: string;
}

export const FilterSelect = forwardRef<HTMLButtonElement, FilterSelectProps>(
  (
    {
      options,
      value,
      onChange,
      placeholder = "Filter...",
      disabled = false,
      loading = false,
      className = "",
      id,
      "aria-label": ariaLabel = "Filter selection",
    },
    ref,
  ) => {
    if (loading) {
      return (
        <div
          className={`h-8 w-32 animate-pulse rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:bg-[#21262d] ${className}`}
        />
      );
    }

    const selectedOption = options.find((opt) => opt.value === value);

    return (
      <Select value={value} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          ref={ref}
          id={id}
          aria-label={ariaLabel}
          className={`h-8 cursor-pointer gap-2 rounded-[var(--kit-radius-control,8px)] border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] px-2.5 font-medium text-[var(--kit-text-primary,#111827)] text-xs transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1f6feb)] dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#f0f6fc] dark:hover:bg-[#21262d] ${className}`}
        >
          <div className="flex items-center gap-1.5 truncate">
            {selectedOption?.icon && (
              <span className="flex shrink-0 items-center text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
                {selectedOption.icon}
              </span>
            )}
            <span className="truncate">{selectedOption?.label ?? placeholder}</span>
          </div>
        </SelectTrigger>
        <SelectContent className="z-50 rounded-[var(--kit-radius-control,8px)] border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] shadow-[var(--kit-shadow-popover)] dark:border-[#30363d] dark:bg-[#161b22]">
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              className="cursor-pointer rounded-[calc(var(--kit-radius-control,8px)-2px)] py-1.5 text-[var(--kit-text-primary,#111827)] text-xs focus:bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] focus:text-[var(--kit-accent,#1f6feb)] dark:text-[#f0f6fc] dark:focus:text-[#58a6ff]"
            >
              <div className="flex items-center gap-2">
                {option.icon && (
                  <span className="flex shrink-0 items-center text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
                    {option.icon}
                  </span>
                )}
                <span>{option.label}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  },
);

FilterSelect.displayName = "FilterSelect";
