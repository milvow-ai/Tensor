import { describe, expect, it } from "vitest";

import {
  formatCompactNumber,
  formatCurrency,
  formatDate,
  formatDuration,
  formatNumber,
  formatPercent,
  formatRelativeTime,
  formatTokens,
  formatValue,
} from "./format";

describe("format module", () => {
  describe("formatNumber", () => {
    it("formats with en-US grouping (standard thousands)", () => {
      expect(formatNumber(350834, "en-US")).toBe("350,834");
      expect(formatNumber(1000000, "en-US")).toBe("1,000,000");
    });

    it("formats with en-IN grouping (lakhs and crores, 3,50,834 requirement)", () => {
      expect(formatNumber(350834, "en-IN")).toBe("3,50,834");
      expect(formatNumber(1000000, "en-IN")).toBe("10,00,000");
      expect(formatNumber(12345678, "en-IN")).toBe("1,23,45,678");
    });

    it("handles zero and negative numbers", () => {
      expect(formatNumber(0, "en-US")).toBe("0");
      expect(formatNumber(-350834, "en-IN")).toBe("-3,50,834");
    });

    it("handles non-finite values safely", () => {
      expect(formatNumber(Number.NaN)).toBe("—");
      expect(formatNumber(Number.POSITIVE_INFINITY)).toBe("—");
    });
  });

  describe("formatCompactNumber", () => {
    it("compacts numbers in en-US", () => {
      expect(formatCompactNumber(1200, "en-US")).toBe("1.2K");
      expect(formatCompactNumber(1500000, "en-US")).toBe("1.5M");
    });
  });

  describe("formatCurrency", () => {
    it("formats USD currency in en-US", () => {
      expect(formatCurrency(18.4, "en-US", "USD")).toBe("$18.40");
      expect(formatCurrency(1250.5, "en-US", "USD")).toBe("$1,250.50");
    });

    it("formats USD in en-IN with Indian grouping", () => {
      const formatted = formatCurrency(350834, "en-IN", "USD");
      expect(formatted).toContain("3,50,834.00");
    });
  });

  describe("formatPercent", () => {
    it("formats percentages with or without sign", () => {
      expect(formatPercent(9.1, "en-US")).toBe("9.1%");
      expect(formatPercent(9.1, "en-US", { showSign: true })).toBe("+9.1%");
      expect(formatPercent(-9.1, "en-US", { showSign: true })).toBe("-9.1%");
      expect(formatPercent(0, "en-US", { showSign: true })).toBe("0%");
    });

    it("handles ratio conversions", () => {
      expect(formatPercent(0.125, "en-US", { isRatio: true, decimals: 1 })).toBe("12.5%");
    });
  });

  describe("formatTokens", () => {
    it("formats tokens with full count or compact", () => {
      expect(formatTokens(350834, "en-IN")).toBe("3,50,834");
      expect(formatTokens(1200000, "en-US", { compact: true })).toBe("1.2M");
      expect(formatTokens(1200000, "en-US", { compact: true, unit: true })).toBe("1.2M tokens");
    });
  });

  describe("formatDuration", () => {
    it("formats seconds, minutes, hours, days", () => {
      expect(formatDuration(45, { unit: "s" })).toBe("45s");
      expect(formatDuration(125, { unit: "s" })).toBe("2m 5s");
      expect(formatDuration(125, { unit: "s", compact: true })).toBe("2m");
      expect(formatDuration(3665, { unit: "s" })).toBe("1h 1m");
      expect(formatDuration(90000, { unit: "s" })).toBe("1d 1h");
      expect(formatDuration(350, { unit: "ms" })).toBe("350ms");
    });
  });

  describe("formatRelativeTime", () => {
    it("formats relative times correctly", () => {
      const now = new Date("2026-10-24T12:00:00Z");
      const past2h = new Date("2026-10-24T10:00:00Z");
      const future3d = new Date("2026-10-27T12:00:00Z");

      expect(formatRelativeTime(past2h, now, "en-US")).toBe("2 hours ago");
      expect(formatRelativeTime(future3d, now, "en-US")).toBe("in 3 days");
      expect(formatRelativeTime(now, now, "en-US")).toBe("just now");
    });
  });

  describe("formatDate", () => {
    it("formats dates with explicit timeZone and locale", () => {
      const date = new Date("2026-10-24T00:00:00Z");
      const formattedUtc = formatDate(date, "en-US", "UTC");
      expect(formattedUtc).toBe("Oct 24, 2026");

      const formattedIndia = formatDate(date, "en-US", "Asia/Kolkata", {
        hour: "numeric",
        minute: "numeric",
      });
      // 00:00 UTC is 05:30 IST
      expect(formattedIndia).toContain("5:30");
    });
  });

  describe("formatValue", () => {
    it("dispatches to the right formatter", () => {
      expect(formatValue(18.4, "usd", "en-US")).toBe("$18.40");
      expect(formatValue(350834, "number", "en-IN")).toBe("3,50,834");
      expect(formatValue(1500000, "compact", "en-US")).toBe("1.5M");
      expect(formatValue(12.5, "percent", "en-US")).toBe("12.5%");
      expect(formatValue(45, "duration")).toBe("45s");
      expect(formatValue("custom string", "raw")).toBe("custom string");
    });
  });
});
