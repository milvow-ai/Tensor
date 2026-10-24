import type React from "react";
import { forwardRef } from "react";

import { Check, CheckCircle2, Clock, KeyRound, Loader2, OctagonAlert, XCircle } from "lucide-react";

export type FarmStatus = "ready" | "running" | "done" | "waiting-for-you" | "failed" | "limited" | "needs-login";

export interface StatusChipProps extends React.HTMLAttributes<HTMLSpanElement> {
  status: FarmStatus;
  label?: string;
  size?: "sm" | "md";
  pulsing?: boolean;
  showIcon?: boolean;
}

interface StatusConfig {
  defaultLabel: string;
  icon: React.ComponentType<{ className?: string }>;
  classes: string;
  iconClasses: string;
}

const STATUS_CONFIG: Record<FarmStatus, StatusConfig> = {
  ready: {
    defaultLabel: "Ready",
    icon: CheckCircle2,
    classes:
      "bg-[var(--kit-positive-soft,#dcfce7)] text-[var(--kit-positive-text,#15803d)] dark:bg-[rgba(63,185,80,0.15)] dark:text-[#56d364]",
    iconClasses: "text-[var(--kit-positive-text,#15803d)] dark:text-[#56d364]",
  },
  running: {
    defaultLabel: "Running",
    icon: Loader2,
    classes:
      "bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] text-[var(--kit-accent,#1f6feb)] dark:bg-[rgba(56,139,253,0.15)] dark:text-[#58a6ff]",
    iconClasses: "text-[var(--kit-accent,#1f6feb)] dark:text-[#58a6ff] animate-spin",
  },
  done: {
    defaultLabel: "Done",
    icon: Check,
    classes:
      "bg-[var(--kit-positive-soft,#dcfce7)] text-[var(--kit-positive-text,#15803d)] dark:bg-[rgba(63,185,80,0.15)] dark:text-[#56d364]",
    iconClasses: "text-[var(--kit-positive-text,#15803d)] dark:text-[#56d364]",
  },
  "waiting-for-you": {
    defaultLabel: "Waiting for you",
    icon: Clock,
    classes:
      "bg-[var(--kit-warning-soft,#fef3c7)] text-[var(--kit-warning-text,#b45309)] dark:bg-[rgba(210,153,34,0.15)] dark:text-[#e3b341]",
    iconClasses: "text-[var(--kit-warning-text,#b45309)] dark:text-[#e3b341]",
  },
  failed: {
    defaultLabel: "Failed",
    icon: XCircle,
    classes:
      "bg-[var(--kit-negative-soft,#fee2e2)] text-[var(--kit-negative-text,#b91c1c)] dark:bg-[rgba(248,81,73,0.15)] dark:text-[#ff7b72]",
    iconClasses: "text-[var(--kit-negative-text,#b91c1c)] dark:text-[#ff7b72]",
  },
  limited: {
    defaultLabel: "Limited",
    icon: OctagonAlert,
    classes:
      "bg-[var(--kit-warning-soft,#fef3c7)] text-[var(--kit-warning-text,#b45309)] dark:bg-[rgba(210,153,34,0.15)] dark:text-[#e3b341]",
    iconClasses: "text-[var(--kit-warning-text,#b45309)] dark:text-[#e3b341]",
  },
  "needs-login": {
    defaultLabel: "Needs login",
    icon: KeyRound,
    classes:
      "bg-[var(--kit-negative-soft,#fee2e2)] text-[var(--kit-negative-text,#b91c1c)] dark:bg-[rgba(248,81,73,0.15)] dark:text-[#ff7b72]",
    iconClasses: "text-[var(--kit-negative-text,#b91c1c)] dark:text-[#ff7b72]",
  },
};

export const StatusChip = forwardRef<HTMLSpanElement, StatusChipProps>(
  ({ status, label, size = "md", pulsing = false, showIcon = true, className = "", ...props }, ref) => {
    const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.ready;
    const displayLabel = label ?? config.defaultLabel;
    const IconComponent = config.icon;

    const sizeClasses = size === "sm" ? "text-[11px] px-2 py-0.5 gap-1" : "text-xs px-2.5 py-1 gap-1.5";

    const iconSize = size === "sm" ? "w-3 h-3" : "w-3.5 h-3.5";

    return (
      <span
        ref={ref}
        role="status"
        aria-label={`Status: ${displayLabel}`}
        className={`inline-flex select-none items-center rounded-full border border-transparent font-medium tracking-tight transition-colors ${config.classes} ${sizeClasses} ${
          pulsing ? "animate-pulse" : ""
        } ${className}`}
        {...props}
      >
        {showIcon && (
          <span aria-hidden="true" className="flex shrink-0 items-center">
            <IconComponent className={`${iconSize} ${config.iconClasses}`} />
          </span>
        )}
        <span>{displayLabel}</span>
      </span>
    );
  },
);

StatusChip.displayName = "StatusChip";
