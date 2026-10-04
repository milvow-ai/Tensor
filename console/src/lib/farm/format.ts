// Pure formatting helpers. Deterministic (en-US, UTC) so server and client render identical text.

const intFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compactFormat = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const usdWhole = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usdCents = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const usdMicro = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 3,
  maximumFractionDigits: 4,
});

export const DASH = "–";

export function fmtInt(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : intFormat.format(value);
}

export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return Math.abs(value) < 10_000 ? intFormat.format(value) : compactFormat.format(value);
}

/** $1,295 for large sums, $12.40 for ordinary ones, $0.0740 for per-unit prices. */
export function fmtUsd(value: number | null | undefined, opts?: { whole?: boolean }): string {
  if (value === null || value === undefined) return DASH;
  if (opts?.whole || Math.abs(value) >= 10_000) return usdWhole.format(value);
  if (value !== 0 && Math.abs(value) < 0.1) return usdMicro.format(value);
  return usdCents.format(value);
}

/** Plan prices: $185 when whole, $9.90 when not (never rounds a price up). */
export function fmtPlanUsd(value: number | null | undefined): string {
  if (value === null || value === undefined) return DASH;
  return Number.isInteger(value) ? usdWhole.format(value) : usdCents.format(value);
}

export function fmtPct(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return DASH;
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return DASH;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/** "2026-10-04 09:12 UTC": stable across server and client, used for hover titles. */
export function fmtAbsolute(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return DASH;
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return DASH;
  const text = time.toISOString();
  return `${text.slice(0, 10)} ${withSeconds ? text.slice(11, 19) : text.slice(11, 16)} UTC`;
}

/** Short relative time: "just now", "5m ago", "in 3h", "2d ago". `nowMs` is injected so renders stay pure. */
export function fmtRelative(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return DASH;
  const target = new Date(iso).getTime();
  if (Number.isNaN(target)) return DASH;
  const delta = Math.round((target - nowMs) / 1000);
  const abs = Math.abs(delta);
  if (abs < 10) return "just now";
  let amount: string;
  if (abs < 60) amount = `${abs}s`;
  else if (abs < 3600) amount = `${Math.round(abs / 60)}m`;
  else if (abs < 86_400) amount = `${Math.round(abs / 3600)}h`;
  else if (abs < 86_400 * 14) amount = `${Math.round(abs / 86_400)}d`;
  else amount = `${Math.round(abs / (86_400 * 7))}w`;
  return delta < 0 ? `${amount} ago` : `in ${amount}`;
}

/** "11m 04s", "1h 52m", "2d 3h" until a future instant; "now" when it has passed. */
export function fmtCountdown(untilIso: string | null | undefined, nowMs: number): string {
  if (!untilIso) return DASH;
  const target = new Date(untilIso).getTime();
  if (Number.isNaN(target)) return DASH;
  const total = Math.max(0, Math.ceil((target - nowMs) / 1000));
  if (total === 0) return "now";
  const days = Math.floor(total / 86_400);
  const hours = Math.floor((total % 86_400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

const UNIT_LABELS: Record<string, string> = { messages: "msg", requests: "req" };

/** "1,850 credits"; units are short words, so we just append them. */
export function fmtQty(value: number | null | undefined, unit?: string | null): string {
  const base = fmtInt(value);
  if (!unit || base === DASH) return base;
  return `${base} ${UNIT_LABELS[unit] ?? unit}`;
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${intFormat.format(count)} ${count === 1 ? singular : plural}`;
}
