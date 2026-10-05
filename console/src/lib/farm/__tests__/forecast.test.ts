import { describe, expect, it } from "vitest";

import {
  calculateForecast,
  calculateMonthForecast,
  FORECAST_METHOD_EXPLANATION,
  getDaysInMonth,
  getElapsedDays,
  isForecastOverBudget,
} from "../forecast";

describe("forecast calculations", () => {
  it("provides the hover explanation method text", () => {
    expect(FORECAST_METHOD_EXPLANATION).toBe("spend ÷ elapsed days × days in month");
  });

  describe("zero spend", () => {
    it("returns 0 forecast when spend is 0", () => {
      expect(calculateForecast(0, 10, 31)).toBe(0);
      expect(calculateForecast(0, 1, 28)).toBe(0);
      expect(calculateForecast(-5, 5, 30)).toBe(0);
    });

    it("calculateMonthForecast returns 0 when spend is 0", () => {
      const res = calculateMonthForecast(0, new Date("2026-03-15T12:00:00Z"));
      expect(res.forecastUsd).toBe(0);
      expect(res.daysInMonth).toBe(31);
    });
  });

  describe("day 1 behavior", () => {
    it("clamps elapsed days to at least 1.0 on day 1 morning", () => {
      // 2 hours into day 1: elapsed = 2/24 = 0.0833 -> clamped to 1.0
      const date = new Date("2026-05-01T02:00:00Z");
      expect(getElapsedDays(date)).toBe(1.0);
    });

    it("does not blow up forecast on day 1", () => {
      // If someone spends $10 in first hour, forecast should be 10 / 1.0 * 31 = $310, not 10 / 0.04 * 31 = $7750
      const date = new Date("2026-05-01T01:00:00Z");
      const res = calculateMonthForecast(10, date);
      expect(res.elapsedDays).toBe(1.0);
      expect(res.daysInMonth).toBe(31);
      expect(res.forecastUsd).toBe(310);
    });
  });

  describe("leap February and year boundaries", () => {
    it("recognizes 29 days in leap February (2024, 2028, 2000)", () => {
      expect(getDaysInMonth(2024, 2)).toBe(29);
      expect(getDaysInMonth(2028, 2)).toBe(29);
      expect(getDaysInMonth(2000, 2)).toBe(29);
    });

    it("recognizes 28 days in non-leap February (2023, 2025, 2026, 2100)", () => {
      expect(getDaysInMonth(2023, 2)).toBe(28);
      expect(getDaysInMonth(2025, 2)).toBe(28);
      expect(getDaysInMonth(2026, 2)).toBe(28);
      expect(getDaysInMonth(2100, 2)).toBe(28); // century not div by 400
    });

    it("forecasts correctly across leap February", () => {
      // Mid-leap Feb: 14.5 days elapsed in Feb 2024, spend $145
      const date = new Date("2024-02-15T12:00:00Z");
      const res = calculateMonthForecast(145, date);
      expect(res.daysInMonth).toBe(29);
      expect(res.elapsedDays).toBeCloseTo(14.5, 1);
      // 145 / 14.5 * 29 = 290
      expect(res.forecastUsd).toBe(290);
    });

    it("forecasts correctly across non-leap February", () => {
      // Mid-non-leap Feb: 14.0 days elapsed in Feb 2025, spend $140
      const date = new Date("2025-02-15T00:00:00Z");
      const res = calculateMonthForecast(140, date);
      expect(res.daysInMonth).toBe(28);
      expect(res.elapsedDays).toBe(14.0);
      // 140 / 14 * 28 = 280
      expect(res.forecastUsd).toBe(280);
    });
  });

  describe("month boundaries and day counts", () => {
    it("handles 30-day months (Apr, Jun, Sep, Nov)", () => {
      expect(getDaysInMonth(2026, 4)).toBe(30);
      expect(getDaysInMonth(2026, 6)).toBe(30);
      expect(getDaysInMonth(2026, 9)).toBe(30);
      expect(getDaysInMonth(2026, 11)).toBe(30);
    });

    it("handles 31-day months (Jan, Mar, May, Jul, Aug, Oct, Dec)", () => {
      expect(getDaysInMonth(2026, 1)).toBe(31);
      expect(getDaysInMonth(2026, 3)).toBe(31);
      expect(getDaysInMonth(2026, 5)).toBe(31);
      expect(getDaysInMonth(2026, 7)).toBe(31);
      expect(getDaysInMonth(2026, 8)).toBe(31);
      expect(getDaysInMonth(2026, 10)).toBe(31);
      expect(getDaysInMonth(2026, 12)).toBe(31);
    });

    it("matches exact spend at month end", () => {
      // On the last day of a 30-day month (e.g. Apr 30 at 23:59:59.999Z), elapsed ≈ 30
      const endOfApril = new Date("2026-04-30T23:59:59Z");
      const res = calculateMonthForecast(300, endOfApril);
      expect(res.daysInMonth).toBe(30);
      expect(res.elapsedDays).toBeCloseTo(30, 0);
      expect(Math.round(res.forecastUsd)).toBe(300);
    });

    it("transitions cleanly from end of month to day 1 of next month", () => {
      const endOfDec = new Date("2025-12-31T23:59:59Z");
      const startOfJan = new Date("2026-01-01T00:00:00Z");

      const decRes = calculateMonthForecast(310, endOfDec);
      expect(decRes.daysInMonth).toBe(31);
      expect(Math.round(decRes.forecastUsd)).toBe(310);

      const janRes = calculateMonthForecast(0, startOfJan);
      expect(janRes.daysInMonth).toBe(31);
      expect(janRes.elapsedDays).toBe(1.0);
      expect(janRes.forecastUsd).toBe(0);
    });
  });

  describe("budget comparison", () => {
    it("flags when forecast strictly exceeds budget", () => {
      expect(isForecastOverBudget(1050, 1000)).toBe(true);
      expect(isForecastOverBudget(1000.01, 1000)).toBe(true);
    });

    it("does not flag when forecast is within or equal to budget", () => {
      expect(isForecastOverBudget(1000, 1000)).toBe(false);
      expect(isForecastOverBudget(950, 1000)).toBe(false);
    });

    it("handles null or undefined budget without flagging", () => {
      expect(isForecastOverBudget(1500, null)).toBe(false);
      expect(isForecastOverBudget(1500, undefined)).toBe(false);
      expect(isForecastOverBudget(1500, 0)).toBe(false);
    });
  });
});
