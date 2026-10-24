import type React from "react";
import { forwardRef } from "react";

import { Sparkles } from "lucide-react";

import { MeterBar } from "./MeterBar";

export interface PlanCap {
  label: string;
  used: number;
  total: number;
  unit?: string;
}

export interface PlanCardProps extends React.HTMLAttributes<HTMLDivElement> {
  plan: string;
  capsUsed: PlanCap[];
  cta?: {
    label: string;
    onClick?: () => void;
    href?: string;
  };
  loading?: boolean;
  error?: string;
}

export const PlanCard = forwardRef<HTMLDivElement, PlanCardProps>(
  ({ plan, capsUsed, cta, loading = false, error, className = "", ...props }, ref) => {
    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-3.5 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="h-4 w-24 rounded bg-[var(--kit-bg-card,#ffffff)] dark:bg-[#21262d]" />
          <div className="h-2 w-full rounded bg-[var(--kit-bg-card,#ffffff)] dark:bg-[#21262d]" />
          <div className="h-7 w-full rounded bg-[var(--kit-bg-card,#ffffff)] dark:bg-[#21262d]" />
        </div>
      );
    }

    if (error) {
      return (
        <div
          ref={ref}
          role="alert"
          className={`rounded-[var(--kit-radius-card,12px)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-3 text-[var(--kit-negative-text,#b91c1c)] text-xs dark:bg-[#161b22] dark:text-[#ff7b72] ${className}`}
          {...props}
        >
          Plan error: {error}
        </div>
      );
    }

    return (
      <div
        ref={ref}
        className={`space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-3.5 text-xs dark:border-[#30363d] dark:bg-[#21262d] ${className}`}
        {...props}
      >
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 font-semibold text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
            <Sparkles className="h-3.5 w-3.5 shrink-0 text-[var(--kit-accent,#1a63d6)] dark:text-[#58a6ff]" />
            <span>{plan}</span>
          </div>
        </div>

        {/* Caps / Limits */}
        <div className="space-y-2.5">
          {capsUsed.map((cap) => {
            const unit = cap.unit ?? "";
            const caption = `${cap.used}${unit} of ${cap.total}${unit}`;
            return (
              <div key={cap.label} className="space-y-1">
                <MeterBar
                  label={cap.label}
                  value={cap.used}
                  max={cap.total}
                  mode="threshold"
                  caption={caption}
                  showValue
                />
              </div>
            );
          })}
        </div>

        {/* CTA Button / Link */}
        {cta && (
          <div className="pt-1">
            {cta.href ? (
              <a
                href={cta.href}
                className="inline-flex w-full cursor-pointer items-center justify-center rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-accent,#1a63d6)] px-3 py-1.5 text-center font-medium text-white shadow-sm transition-colors hover:bg-[var(--kit-accent-hover,#195ac0)]"
              >
                {cta.label}
              </a>
            ) : (
              <button
                type="button"
                onClick={cta.onClick}
                className="inline-flex w-full cursor-pointer items-center justify-center rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-accent,#1a63d6)] px-3 py-1.5 text-center font-medium text-white shadow-sm transition-colors hover:bg-[var(--kit-accent-hover,#195ac0)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1a63d6)]"
              >
                {cta.label}
              </button>
            )}
          </div>
        )}
      </div>
    );
  },
);

PlanCard.displayName = "PlanCard";
