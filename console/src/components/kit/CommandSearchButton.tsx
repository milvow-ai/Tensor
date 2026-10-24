import type React from "react";
import { forwardRef } from "react";

import { Search } from "lucide-react";

export interface CommandSearchButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  shortcut?: string;
  placeholder?: string;
  loading?: boolean;
}

export const CommandSearchButton = forwardRef<HTMLButtonElement, CommandSearchButtonProps>(
  (
    {
      shortcut = "⌘K",
      placeholder = "Search or jump to...",
      loading = false,
      onClick,
      className = "",
      disabled,
      ...props
    },
    ref,
  ) => {
    if (loading) {
      return (
        <div
          className={`h-9 w-60 animate-pulse rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:bg-[#21262d] ${className}`}
        />
      );
    }

    return (
      <button
        ref={ref}
        type="button"
        disabled={disabled}
        onClick={onClick}
        aria-label={`${placeholder} (${shortcut})`}
        className={`flex h-9 w-60 cursor-pointer select-none items-center justify-between rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] px-3 text-[var(--kit-text-secondary,#6b7280)] text-xs transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] hover:text-[var(--kit-text-primary,#111827)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1f6feb)] dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#8b949e] dark:hover:bg-[#21262d] dark:hover:text-[#f0f6fc] ${className}`}
        {...props}
      >
        <span className="flex items-center gap-2 truncate">
          <Search className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">{placeholder}</span>
        </span>
        <kbd className="inline-flex shrink-0 items-center rounded border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] px-1.5 py-0.5 font-medium font-mono text-[10px] text-[var(--kit-text-secondary,#6b7280)] dark:border-[#30363d] dark:bg-[#21262d] dark:text-[#8b949e]">
          {shortcut}
        </kbd>
      </button>
    );
  },
);

CommandSearchButton.displayName = "CommandSearchButton";
