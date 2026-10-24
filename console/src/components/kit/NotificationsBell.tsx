import type React from "react";
import { forwardRef } from "react";

import { Bell } from "lucide-react";

export interface NotificationsBellProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  count?: number;
  hasUnread?: boolean;
  loading?: boolean;
}

export const NotificationsBell = forwardRef<HTMLButtonElement, NotificationsBellProps>(
  ({ count, hasUnread, loading = false, onClick, className = "", disabled, ...props }, ref) => {
    if (loading) {
      return (
        <div
          className={`h-9 w-9 animate-pulse rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:bg-[#21262d] ${className}`}
        />
      );
    }

    const showBadge = (typeof count === "number" && count > 0) || hasUnread;
    let badgeText = "";
    if (typeof count === "number" && count > 0) {
      badgeText = count > 99 ? "99+" : String(count);
    }

    let ariaLabel = "Notifications";
    if (typeof count === "number" && count > 0) {
      ariaLabel = `Notifications, ${count} unread`;
    } else if (showBadge) {
      ariaLabel = "Notifications, new items available";
    }

    return (
      <button
        ref={ref}
        type="button"
        disabled={disabled}
        onClick={onClick}
        aria-label={ariaLabel}
        className={`relative inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] text-[var(--kit-text-secondary,#5b6370)] transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] hover:text-[var(--kit-text-primary,#111827)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1a63d6)] disabled:cursor-not-allowed disabled:opacity-40 dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#8b949e] dark:hover:bg-[#21262d] dark:hover:text-[#f0f6fc] ${className}`}
        {...props}
      >
        <Bell className="h-4 w-4" />
        {showBadge && (
          <span
            aria-hidden="true"
            className={`absolute ${
              badgeText
                ? "-top-1 -right-1 h-4 min-w-[16px] rounded-full px-1 font-bold text-[10px]"
                : "top-2 right-2 h-2 w-2 rounded-full"
            } flex items-center justify-center bg-[var(--kit-accent,#1a63d6)] text-white ring-2 ring-[var(--kit-bg-card,#ffffff)] dark:ring-[#161b22]`}
          >
            {badgeText}
          </span>
        )}
      </button>
    );
  },
);

NotificationsBell.displayName = "NotificationsBell";
