// Month-end spend forecast calculation.
// Method: spend ÷ elapsed days × days in month.
// Shown on hover, flagged when forecast > budget.

export const FORECAST_METHOD_EXPLANATION = "spend ÷ elapsed days × days in month";

/**
 * Returns the number of days in the given month (1-indexed: 1 = Jan, 12 = Dec).
 * Properly accounts for leap years (including leap February).
 */
export function getDaysInMonth(year: number, month: number): number {
  // Date(year, month, 0) gives the last day of month (when month is 1-indexed)
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * Computes fractional elapsed days since the start of the UTC month for `now`.
 * Guarded to at least 1.0 day to avoid day-1 blowups and division by zero.
 */
export function getElapsedDays(now: Date | number | string = Date.now()): number {
  const date = typeof now === "object" ? now : new Date(now);
  const nowMs = date.getTime();
  const monthStartMs = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
  const elapsed = (nowMs - monthStartMs) / 86_400_000;
  return Math.max(elapsed, 1.0);
}

/**
 * Calculates month-end forecast from spend, elapsed days, and total days in month.
 * Rounds to 2 decimal places (cents). Zero spend yields zero forecast.
 */
export function calculateForecast(spend: number, elapsedDays: number, daysInMonth: number): number {
  if (spend <= 0) return 0;
  const safeElapsed = Math.max(elapsedDays, 1.0);
  const raw = (spend / safeElapsed) * daysInMonth;
  return Math.round(raw * 100) / 100;
}

/**
 * Calculates month forecast directly for a given date and spend amount.
 */
export function calculateMonthForecast(
  spend: number,
  now: Date | number | string = Date.now(),
): {
  forecastUsd: number;
  elapsedDays: number;
  daysInMonth: number;
} {
  const date = typeof now === "object" ? now : new Date(now);
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth() + 1;
  const daysInMonth = getDaysInMonth(year, month);
  const elapsedDays = getElapsedDays(date);
  const forecastUsd = calculateForecast(spend, elapsedDays, daysInMonth);

  return {
    forecastUsd,
    elapsedDays,
    daysInMonth,
  };
}

/**
 * Returns true if forecast exceeds the budget cap.
 */
export function isForecastOverBudget(forecast: number, budget: number | null | undefined): boolean {
  if (budget === null || budget === undefined || budget <= 0) return false;
  return forecast > budget;
}
