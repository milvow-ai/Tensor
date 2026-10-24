import type React from "react";
import { forwardRef } from "react";

import { Check, Circle, Sparkles } from "lucide-react";

import { MeterBar } from "./MeterBar";

export interface OnboardingStep {
  id: string;
  title: string;
  description?: string;
  completed: boolean;
  current?: boolean;
  action?: {
    label: string;
    onClick?: () => void;
    href?: string;
  };
}

export interface OnboardingChecklistProps extends React.HTMLAttributes<HTMLDivElement> {
  steps: OnboardingStep[];
  title?: string;
  subtitle?: string;
  loading?: boolean;
}

export const OnboardingChecklist = forwardRef<HTMLDivElement, OnboardingChecklistProps>(
  (
    {
      steps,
      title = "Get started with your Farm",
      subtitle = "Four essential steps to set up your pool of AIs, tools and agents.",
      loading = false,
      className = "",
      ...props
    },
    ref,
  ) => {
    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-6 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="space-y-2">
            <div className="h-5 w-48 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-3.5 w-72 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="h-2 w-full rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="space-y-3 pt-2">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="h-14 w-full rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            ))}
          </div>
        </div>
      );
    }

    const completedCount = steps.filter((s) => s.completed).length;
    const progressCaption = `${completedCount} of ${steps.length} completed`;

    return (
      <div
        ref={ref}
        className={`space-y-5 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-6 text-xs shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {/* Header */}
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-[var(--kit-accent,#1a63d6)] dark:text-[#58a6ff]" />
            <h3 className="font-bold text-[var(--kit-text-primary,#111827)] text-base dark:text-[#f0f6fc]">{title}</h3>
          </div>
          {subtitle && (
            <p className="text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">{subtitle}</p>
          )}
        </div>

        {/* Progress meter */}
        <MeterBar
          value={completedCount}
          max={steps.length}
          mode="category"
          color="var(--kit-accent, #1f6feb)"
          caption={progressCaption}
          showValue
        />

        {/* Steps list */}
        <div className="space-y-2.5 pt-1">
          {steps.map((step, idx) => {
            const isCompleted = step.completed;
            const isCurrent = step.current ?? (!isCompleted && steps.slice(0, idx).every((s) => s.completed));

            let containerClasses =
              "border-[var(--kit-border,#e6e8ec)] dark:border-[#30363d] bg-[var(--kit-bg-card,#ffffff)] dark:bg-[#161b22]";
            if (isCompleted) {
              containerClasses =
                "border-[var(--kit-border,#e6e8ec)] dark:border-[#30363d] bg-[var(--kit-bg-card,#ffffff)] dark:bg-[#161b22] opacity-80";
            } else if (isCurrent) {
              containerClasses =
                "border-[var(--kit-accent,#1a63d6)]/60 bg-[var(--kit-accent-soft,rgba(31,111,235,0.06))] dark:bg-[rgba(56,139,253,0.1)]";
            }

            let statusIndicator: React.ReactNode = (
              <Circle className="h-5 w-5 text-[var(--kit-text-muted,#9ca3af)] dark:text-[#6e7681]" />
            );
            if (isCompleted) {
              statusIndicator = (
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--kit-positive-soft,#dcfce7)] font-bold text-[var(--kit-positive-text,#15803d)] dark:bg-[rgba(63,185,80,0.15)] dark:text-[#56d364]">
                  <Check className="h-3.5 w-3.5" />
                </span>
              );
            } else if (isCurrent) {
              statusIndicator = (
                <span className="flex h-5 w-5 items-center justify-center rounded-full border-2 border-[var(--kit-accent,#1a63d6)] dark:border-[#58a6ff]">
                  <span className="h-2 w-2 rounded-full bg-[var(--kit-accent,#1a63d6)] dark:bg-[#58a6ff]" />
                </span>
              );
            }

            return (
              <div
                key={step.id}
                className={`flex flex-col justify-between gap-3 rounded-[var(--kit-radius-control,8px)] border p-3.5 transition-all sm:flex-row sm:items-center ${containerClasses}`}
              >
                <div className="flex items-start gap-3">
                  <div className="shrink-0 pt-0.5">{statusIndicator}</div>

                  <div className="space-y-0.5">
                    <h4
                      className={`font-semibold text-xs ${
                        isCompleted
                          ? "text-[var(--kit-text-secondary,#5b6370)] line-through dark:text-[#8b949e]"
                          : "text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]"
                      }`}
                    >
                      {step.title}
                    </h4>
                    {step.description && (
                      <p className="text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
                        {step.description}
                      </p>
                    )}
                  </div>
                </div>

                {step.action && !isCompleted && (
                  <div className="shrink-0 pl-8 sm:self-center sm:pl-0">
                    {step.action.href ? (
                      <a
                        href={step.action.href}
                        className={`inline-flex items-center justify-center rounded-[var(--kit-radius-control,8px)] px-3 py-1.5 font-medium text-xs transition-colors ${
                          isCurrent
                            ? "bg-[var(--kit-accent,#1a63d6)] text-white shadow-sm hover:bg-[var(--kit-accent-hover,#195ac0)]"
                            : "border border-[var(--kit-border,#e6e8ec)] text-[var(--kit-text-primary,#111827)] hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:text-[#f0f6fc]"
                        }`}
                      >
                        {step.action.label}
                      </a>
                    ) : (
                      <button
                        type="button"
                        onClick={step.action.onClick}
                        className={`inline-flex cursor-pointer items-center justify-center rounded-[var(--kit-radius-control,8px)] px-3 py-1.5 font-medium text-xs transition-colors ${
                          isCurrent
                            ? "bg-[var(--kit-accent,#1a63d6)] text-white shadow-sm hover:bg-[var(--kit-accent-hover,#195ac0)]"
                            : "border border-[var(--kit-border,#e6e8ec)] text-[var(--kit-text-primary,#111827)] hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:text-[#f0f6fc]"
                        }`}
                      >
                        {step.action.label}
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  },
);

OnboardingChecklist.displayName = "OnboardingChecklist";
