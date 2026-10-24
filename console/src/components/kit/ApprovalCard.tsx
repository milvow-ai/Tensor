"use client";

import type React from "react";
import { forwardRef, useEffect } from "react";

import { Check, ShieldAlert, X } from "lucide-react";

import { formatCurrency } from "@/lib/format";

export type RiskLevel = "low" | "medium" | "high";

export interface ApprovalWhat {
  tool: string;
  action: string;
  target: string;
}

export interface ApprovalWho {
  operator: string;
  agent: string;
}

export interface ApprovalCardProps extends React.HTMLAttributes<HTMLDivElement> {
  what: ApprovalWhat;
  why: string;
  who: ApprovalWho;
  costSoFar: number | string;
  risk: RiskLevel;
  onApprove: () => void;
  onDeny: () => void;
  loading?: boolean;
  disabled?: boolean;
  enableGlobalKeyboardShortcuts?: boolean;
}

const RISK_CONFIG: Record<RiskLevel, { label: string; classes: string }> = {
  low: {
    label: "Low Risk",
    classes:
      "bg-[var(--kit-positive-soft,#dcfce7)] text-[var(--kit-positive-text,#15803d)] dark:bg-[rgba(63,185,80,0.15)] dark:text-[#56d364]",
  },
  medium: {
    label: "Medium Risk",
    classes:
      "bg-[var(--kit-warning-soft,#fef3c7)] text-[var(--kit-warning-text,#b45309)] dark:bg-[rgba(210,153,34,0.15)] dark:text-[#e3b341]",
  },
  high: {
    label: "High Risk",
    classes:
      "bg-[var(--kit-negative-soft,#fee2e2)] text-[var(--kit-negative-text,#b91c1c)] dark:bg-[rgba(248,81,73,0.15)] dark:text-[#ff7b72]",
  },
};

export const ApprovalCard = forwardRef<HTMLDivElement, ApprovalCardProps>(
  (
    {
      what,
      why,
      who,
      costSoFar,
      risk,
      onApprove,
      onDeny,
      loading = false,
      disabled = false,
      enableGlobalKeyboardShortcuts = false,
      className = "",
      ...props
    },
    ref,
  ) => {
    useEffect(() => {
      if (!enableGlobalKeyboardShortcuts || disabled || loading) return;

      const handleKeyDown = (e: KeyboardEvent) => {
        // Only trigger if not typing in an input
        const tag = (e.target as HTMLElement)?.tagName?.toLowerCase();
        if (tag === "input" || tag === "textarea") return;

        if (e.key === "a" || e.key === "A") {
          e.preventDefault();
          onApprove();
        } else if (e.key === "d" || e.key === "D") {
          e.preventDefault();
          onDeny();
        }
      };

      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }, [enableGlobalKeyboardShortcuts, disabled, loading, onApprove, onDeny]);

    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="flex justify-between">
            <div className="h-4 w-40 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-4 w-16 rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="h-10 w-full rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="flex gap-2 pt-2">
            <div className="h-9 flex-1 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-9 flex-1 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
        </div>
      );
    }

    const formattedCost = typeof costSoFar === "number" ? formatCurrency(costSoFar) : costSoFar;
    const riskInfo = RISK_CONFIG[risk] ?? RISK_CONFIG.medium;

    return (
      <div
        ref={ref}
        className={`space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 text-xs shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {/* Header: Action / Target + Risk Badge */}
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
                {what.tool}
              </span>
              <span className="rounded bg-[var(--kit-bg-subtle,#f3f4f6)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:bg-[#21262d] dark:text-[#8b949e]">
                {what.action}
              </span>
            </div>
            <p className="truncate font-mono text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
              Target: <span className="text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">{what.target}</span>
            </p>
          </div>

          <span
            className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 font-semibold text-[11px] ${riskInfo.classes}`}
          >
            {risk === "high" && <ShieldAlert className="h-3 w-3" />}
            {riskInfo.label}
          </span>
        </div>

        {/* Why statement */}
        <div className="rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-3 text-[var(--kit-text-primary,#111827)] leading-relaxed dark:border-[#30363d] dark:bg-[#21262d] dark:text-[#f0f6fc]">
          <span className="mb-1 block font-semibold text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
            Agent's reason:
          </span>
          {why}
        </div>

        {/* Metadata: who & cost */}
        <div className="flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-t pt-1 text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:border-[#30363d] dark:text-[#8b949e]">
          <div>
            Requested by{" "}
            <span className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">{who.agent}</span>{" "}
            on behalf of{" "}
            <span className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
              {who.operator}
            </span>
          </div>
          <div className="tabular-nums">
            Cost so far:{" "}
            <span className="font-semibold text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
              {formattedCost}
            </span>
          </div>
        </div>

        {/* Buttons: Deny & Approve with [D] and [A] shortcut keys */}
        <div className="flex items-center gap-2.5 pt-1">
          <button
            type="button"
            disabled={disabled}
            onClick={onDeny}
            className="inline-flex flex-1 cursor-pointer select-none items-center justify-center gap-1.5 rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] px-3 py-2 font-medium text-[var(--kit-negative-text,#b91c1c)] transition-colors hover:bg-[var(--kit-negative-soft,#fee2e2)]/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-negative,#dc2626)] disabled:opacity-40 dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#ff7b72]"
          >
            <X className="h-3.5 w-3.5" />
            <span>Deny</span>
            <kbd className="ml-1 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] px-1 font-mono text-[10px] text-[var(--kit-text-secondary,#5b6370)] dark:bg-[#21262d] dark:text-[#8b949e]">
              D
            </kbd>
          </button>

          <button
            type="button"
            disabled={disabled}
            onClick={onApprove}
            className="inline-flex flex-1 cursor-pointer select-none items-center justify-center gap-1.5 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-accent,#1a63d6)] px-3 py-2 font-medium text-white shadow-sm transition-colors hover:bg-[var(--kit-accent-hover,#195ac0)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1a63d6)] disabled:opacity-40"
          >
            <Check className="h-3.5 w-3.5" />
            <span>Approve</span>
            <kbd className="ml-1 rounded bg-blue-700/50 px-1 font-mono text-[10px] text-blue-100">A</kbd>
          </button>
        </div>
      </div>
    );
  },
);

ApprovalCard.displayName = "ApprovalCard";
