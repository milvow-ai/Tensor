/**
 * Unified formatting utilities for Harness Farm Console
 * Built on `Intl` with explicit locale and time-zone support.
 */

export interface NumberFormatConfig extends Intl.NumberFormatOptions {
  locale?: string;
}

/**
 * Format standard numbers with locale-aware grouping (e.g. 3,50,834 in en-IN, 350,834 in en-US)
 */
export function formatNumber(value: number, locale = "en-US", options?: Intl.NumberFormatOptions): string {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, options).format(value);
}

/**
 * Format compact numbers (e.g., 1.2K, 3.4M, 1.5Cr depending on locale)
 */
export function formatCompactNumber(value: number, locale = "en-US", options?: Intl.NumberFormatOptions): string {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumFractionDigits: 1,
    ...options,
  }).format(value);
}

/**
 * Format currency with explicit currency code (default USD) and locale
 */
export function formatCurrency(
  value: number,
  locale = "en-US",
  currency = "USD",
  options?: Intl.NumberFormatOptions,
): string {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    ...options,
  }).format(value);
}

export interface PercentOptions {
  decimals?: number;
  showSign?: boolean;
  isRatio?: boolean; // if true, 0.12 becomes 12%; if false, 12 becomes 12%
}

/**
 * Format percentage with optional sign and precision
 */
export function formatPercent(value: number, locale = "en-US", options: PercentOptions = {}): string {
  if (!Number.isFinite(value)) return "—";
  const { decimals = 1, showSign = false, isRatio = false } = options;
  const numValue = isRatio ? value * 100 : value;

  const formattedNum = new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  }).format(Math.abs(numValue));

  if (showSign) {
    if (numValue > 0) return `+${formattedNum}%`;
    if (numValue < 0) return `-${formattedNum}%`;
    return `${formattedNum}%`;
  }

  return numValue < 0 ? `-${formattedNum}%` : `${formattedNum}%`;
}

/**
 * Format token quantities (e.g. 1,250,000 or 1.2M tokens)
 */
export function formatTokens(
  tokens: number,
  locale = "en-US",
  options: { compact?: boolean; unit?: boolean } = {},
): string {
  if (!Number.isFinite(tokens)) return "—";
  const { compact = false, unit = false } = options;
  const numStr = compact ? formatCompactNumber(tokens, locale) : formatNumber(tokens, locale);

  return unit ? `${numStr} tokens` : numStr;
}

export interface DurationOptions {
  unit?: "ms" | "s";
  compact?: boolean;
}

/**
 * Format duration into human-readable strings (e.g. 350ms, 45s, 3m 12s, 2h 15m, 3d 4h)
 */
export function formatDuration(value: number, options: DurationOptions = {}): string {
  if (!Number.isFinite(value) || value < 0) return "0s";
  const { unit = "s", compact = false } = options;
  const totalSeconds = unit === "ms" ? Math.floor(value / 1000) : Math.floor(value);
  const ms = unit === "ms" ? Math.floor(value % 1000) : 0;

  if (totalSeconds === 0) {
    if (unit === "ms") return `${ms}ms`;
    return "0s";
  }

  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  if (days > 0) {
    if (compact) return `${days}d`;
    return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  }

  if (hours > 0) {
    if (compact) return `${hours}h`;
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }

  if (minutes > 0) {
    if (compact) return `${minutes}m`;
    return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
  }

  return `${seconds}s`;
}

/**
 * Format relative time (e.g. "2 hours ago", "in 3 days", "just now")
 */
export function formatRelativeTime(
  dateOrTimestamp: Date | number | string,
  baseDate: Date = new Date(),
  locale = "en-US",
): string {
  const targetDate =
    typeof dateOrTimestamp === "string" || typeof dateOrTimestamp === "number"
      ? new Date(dateOrTimestamp)
      : dateOrTimestamp;

  if (Number.isNaN(targetDate.getTime())) return "—";

  const diffInSeconds = Math.round((targetDate.getTime() - baseDate.getTime()) / 1000);

  if (Math.abs(diffInSeconds) < 30) {
    return "just now";
  }

  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

  const cutoffs = [
    { unit: "year" as const, threshold: 31536000 },
    { unit: "month" as const, threshold: 2592000 },
    { unit: "week" as const, threshold: 604800 },
    { unit: "day" as const, threshold: 86400 },
    { unit: "hour" as const, threshold: 3600 },
    { unit: "minute" as const, threshold: 60 },
  ];

  for (const { unit, threshold } of cutoffs) {
    if (Math.abs(diffInSeconds) >= threshold) {
      const delta = Math.round(diffInSeconds / threshold);
      return rtf.format(delta, unit);
    }
  }

  return rtf.format(diffInSeconds, "second");
}

/**
 * Format date/time with explicit locale and time-zone
 */
export function formatDate(
  dateOrTimestamp: Date | number | string,
  locale = "en-US",
  timeZone = "UTC",
  options?: Intl.DateTimeFormatOptions,
): string {
  const date =
    typeof dateOrTimestamp === "string" || typeof dateOrTimestamp === "number"
      ? new Date(dateOrTimestamp)
      : dateOrTimestamp;

  if (Number.isNaN(date.getTime())) return "—";

  const defaultOptions: Intl.DateTimeFormatOptions = {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone,
    ...options,
  };

  return new Intl.DateTimeFormat(locale, defaultOptions).format(date);
}

export type ValueFormatType = "number" | "compact" | "usd" | "currency" | "percent" | "tokens" | "duration" | "raw";

/**
 * Generic value formatter dispatch
 */
export function formatValue(
  value: number | string,
  format: ValueFormatType = "number",
  locale = "en-US",
  _timeZone = "UTC",
): string {
  if (typeof value === "string") return value;
  if (!Number.isFinite(value)) return "—";

  switch (format) {
    case "compact":
      return formatCompactNumber(value, locale);
    case "usd":
    case "currency":
      return formatCurrency(value, locale, "USD");
    case "percent":
      return formatPercent(value, locale, { showSign: false });
    case "tokens":
      return formatTokens(value, locale);
    case "duration":
      return formatDuration(value);
    case "raw":
      return String(value);
    default:
      return formatNumber(value, locale);
  }
}
