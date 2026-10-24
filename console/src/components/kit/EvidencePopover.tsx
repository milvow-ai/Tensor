"use client";

import type React from "react";
import { forwardRef, useState } from "react";

import { HelpCircle, RefreshCw, Terminal } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { formatDate, formatRelativeTime } from "@/lib/format";

export interface EvidencePopoverProps {
  observedExcerpt: string;
  sourceRun?: {
    id: string;
    label?: string;
  };
  checkedAt: Date | string;
  onRecheck?: () => void | Promise<void>;
  isRechecking?: boolean;
  state?: string;
  children?: React.ReactNode;
  align?: "start" | "center" | "end";
}

export const EvidencePopover = forwardRef<HTMLDivElement, EvidencePopoverProps>(
  (
    {
      observedExcerpt,
      sourceRun,
      checkedAt,
      onRecheck,
      isRechecking: controlledRechecking,
      state,
      children,
      align = "start",
    },
    ref,
  ) => {
    const [internalRechecking, setInternalRechecking] = useState(false);
    const isRechecking = controlledRechecking ?? internalRechecking;

    const handleRecheck = async () => {
      if (!onRecheck) return;
      try {
        setInternalRechecking(true);
        await onRecheck();
      } finally {
        setInternalRechecking(false);
      }
    };

    const checkedDate = typeof checkedAt === "string" ? new Date(checkedAt) : checkedAt;
    const relativeChecked = formatRelativeTime(checkedDate);
    const absoluteChecked = formatDate(checkedDate, "en-US", "UTC", {
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
    });

    return (
      <Popover>
        <PopoverTrigger asChild>
          {children ?? (
            <button
              type="button"
              className="inline-flex cursor-pointer select-none items-center gap-1 text-[var(--kit-accent,#1f6feb)] text-xs hover:underline dark:text-[#58a6ff]"
              aria-label="View status evidence"
            >
              <HelpCircle className="h-3.5 w-3.5" />
              <span>Why ▸</span>
            </button>
          )}
        </PopoverTrigger>

        <PopoverContent
          ref={ref}
          align={align}
          className="z-50 w-80 space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-3.5 text-xs shadow-[var(--kit-shadow-popover)] dark:border-[#30363d] dark:bg-[#161b22]"
        >
          {/* Header */}
          <div className="flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b pb-2 dark:border-[#30363d]">
            <div className="flex items-center gap-1.5 font-semibold text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
              <Terminal className="h-3.5 w-3.5 text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]" />
              <span>Observed Evidence</span>
            </div>
            {state && (
              <span className="rounded bg-[var(--kit-bg-subtle,#f3f4f6)] px-1.5 py-0.5 font-bold text-[10px] text-[var(--kit-text-secondary,#6b7280)] uppercase tracking-wider dark:bg-[#21262d] dark:text-[#8b949e]">
                {state}
              </span>
            )}
          </div>

          {/* Excerpt Code Block */}
          <div className="max-h-32 overflow-y-auto break-words rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-2 font-mono text-[11px] text-[var(--kit-text-primary,#111827)] leading-relaxed dark:border-[#30363d] dark:bg-[#0d1117] dark:text-[#f0f6fc]">
            {observedExcerpt}
          </div>

          {/* Metadata: source run + checked at */}
          <div className="space-y-1 text-[11px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
            {sourceRun && (
              <div className="flex justify-between">
                <span>Source run:</span>
                <span className="font-mono text-[var(--kit-accent,#1f6feb)] dark:text-[#58a6ff]">
                  {sourceRun.label ?? sourceRun.id}
                </span>
              </div>
            )}
            <div className="flex justify-between" title={absoluteChecked}>
              <span>Checked:</span>
              <span className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
                {relativeChecked}
              </span>
            </div>
          </div>

          {/* Re-check action */}
          {onRecheck && (
            <div className="border-[var(--kit-border,#e6e8ec)] border-t pt-2 dark:border-[#30363d]">
              <button
                type="button"
                onClick={handleRecheck}
                disabled={isRechecking}
                className="inline-flex w-full cursor-pointer select-none items-center justify-center gap-1.5 rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] px-3 py-1.5 font-medium text-[var(--kit-text-primary,#111827)] transition-colors hover:bg-[var(--kit-border,#e6e8ec)] disabled:cursor-not-allowed disabled:opacity-50 dark:border-[#30363d] dark:bg-[#21262d] dark:text-[#f0f6fc]"
              >
                <RefreshCw className={`h-3 w-3 ${isRechecking ? "animate-spin" : ""}`} />
                <span>{isRechecking ? "Re-checking..." : "Re-check now"}</span>
              </button>
            </div>
          )}
        </PopoverContent>
      </Popover>
    );
  },
);

EvidencePopover.displayName = "EvidencePopover";
