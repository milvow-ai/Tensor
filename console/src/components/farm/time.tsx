"use client";

import { createContext, useContext, useEffect, useState } from "react";

import { DASH, fmtAbsolute, fmtCountdown, fmtRelative } from "@/lib/farm/format";

// The server renders every page at one instant. Passing it down keeps server and client text identical on the first
// render (no hydration mismatch); after mount the components tick forward on their own.
const NowContext = createContext<number>(0);

export function NowProvider({ value, children }: { value: number; children: React.ReactNode }) {
  return <NowContext.Provider value={value}>{children}</NowContext.Provider>;
}

function useTicker(intervalMs: number): number {
  const serverNow = useContext(NowContext);
  const [tick, setTick] = useState<number | null>(null);
  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return tick ?? serverNow;
}

/** "5m ago" with the absolute UTC time on hover. */
export function RelativeTime({ iso, className }: { iso: string | null | undefined; className?: string }) {
  const now = useTicker(15_000);
  if (!iso) return <span className={className}>{DASH}</span>;
  return (
    <time dateTime={iso} title={fmtAbsolute(iso, true)} className={className} suppressHydrationWarning>
      {fmtRelative(iso, now)}
    </time>
  );
}

/** Live "11m 04s" until a moment; absolute time on hover. */
export function Countdown({ until, className }: { until: string | null | undefined; className?: string }) {
  const now = useTicker(1000);
  if (!until) return <span className={className}>{DASH}</span>;
  return (
    <time dateTime={until} title={fmtAbsolute(until, true)} className={className} suppressHydrationWarning>
      {fmtCountdown(until, now)}
    </time>
  );
}

/** "in 12d" for a future reset, with the absolute date on hover. */
export function ResetTime({ iso, className }: { iso: string | null | undefined; className?: string }) {
  const now = useTicker(30_000);
  if (!iso) return <span className={className}>{DASH}</span>;
  return (
    <time dateTime={iso} title={fmtAbsolute(iso)} className={className} suppressHydrationWarning>
      {fmtRelative(iso, now)}
    </time>
  );
}
