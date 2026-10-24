import type React from "react";
import { forwardRef } from "react";

import { ArrowDown, ArrowUp, Minus } from "lucide-react";

export type GoodDirection = "up" | "down";
export type DeltaTone = "positive" | "negative" | "neutral";

export interface DeltaPillProps extends React.HTMLAttributes<HTMLSpanElement> {
  value: number;
  goodDirection?: GoodDirection;
  neutralBelow?: number;
  unit?: string;
  decimals?: number;
  showArrow?: boolean;
}

export function computeDeltaMetadata(
  value: number,
  goodDirection: GoodDirection = "up",
  neutralBelow = 0,
  decimals = 1,
  unit = "%",
): {
  tone: DeltaTone;
  isUp: boolean;
  isNeutral: boolean;
  formattedText: string;
  ariaLabel: string;
} {
  const absValue = Math.abs(value);
  const isNeutral = absValue <= neutralBelow;

  const formattedNum = absValue.toFixed(decimals).replace(/\.0+$/, "");
  const formattedText = `${formattedNum}${unit}`;

  if (isNeutral) {
    return {
      tone: "neutral",
      isUp: false,
      isNeutral: true,
      formattedText,
      ariaLabel: `${formattedText}, no change`,
    };
  }

  const isUp = value > 0;
  // If goodDirection is up: up is positive (better), down is negative (worse)
  // If goodDirection is down: down is positive (better, e.g. spend down), up is negative (worse)
  const isPositive = (isUp && goodDirection === "up") || (!isUp && goodDirection === "down");
  const tone: DeltaTone = isPositive ? "positive" : "negative";

  const directionWord = isUp ? "up" : "down";
  const sentimentWord = isPositive ? "better" : "worse";
  const ariaLabel = `${directionWord} ${formattedText}, ${sentimentWord}`;

  return {
    tone,
    isUp,
    isNeutral: false,
    formattedText,
    ariaLabel,
  };
}

export const DeltaPill = forwardRef<HTMLSpanElement, DeltaPillProps>(
  (
    {
      value,
      goodDirection = "up",
      neutralBelow = 0,
      unit = "%",
      decimals = 1,
      showArrow = true,
      className = "",
      ...props
    },
    ref,
  ) => {
    const { tone, isUp, isNeutral, formattedText, ariaLabel } = computeDeltaMetadata(
      value,
      goodDirection,
      neutralBelow,
      decimals,
      unit,
    );

    const toneClasses = {
      positive:
        "bg-[var(--kit-positive-soft,#dcfce7)] text-[var(--kit-positive-text,#15803d)] dark:bg-[rgba(63,185,80,0.15)] dark:text-[#56d364]",
      negative:
        "bg-[var(--kit-negative-soft,#fee2e2)] text-[var(--kit-negative-text,#b91c1c)] dark:bg-[rgba(248,81,73,0.15)] dark:text-[#ff7b72]",
      neutral:
        "bg-[var(--kit-neutral-soft,#f3f4f6)] text-[var(--kit-neutral-text,#4b5563)] dark:bg-[rgba(139,148,158,0.15)] dark:text-[#8b949e]",
    }[tone];

    let arrowIcon: React.ReactNode = null;
    if (showArrow) {
      if (isNeutral) {
        arrowIcon = <Minus className="h-3 w-3" />;
      } else if (isUp) {
        arrowIcon = <ArrowUp className="h-3 w-3" />;
      } else {
        arrowIcon = <ArrowDown className="h-3 w-3" />;
      }
    }

    return (
      <span
        ref={ref}
        role="status"
        aria-label={ariaLabel}
        className={`inline-flex select-none items-center gap-1 rounded-full px-2 py-0.5 font-semibold text-xs tabular-nums tracking-tight transition-colors ${toneClasses} ${className}`}
        {...props}
      >
        {showArrow && (
          <span aria-hidden="true" className="shrink-0">
            {arrowIcon}
          </span>
        )}
        <span>{formattedText}</span>
      </span>
    );
  },
);

DeltaPill.displayName = "DeltaPill";
