import { describe, expect, it } from "vitest";

import { computeDeltaMetadata } from "./DeltaPill";

describe("DeltaPill logic", () => {
  describe("when goodDirection is 'up' (default, e.g. tasks done, revenue)", () => {
    it("marks positive change as positive / green ('better')", () => {
      const meta = computeDeltaMetadata(12.4, "up");
      expect(meta.tone).toBe("positive");
      expect(meta.isUp).toBe(true);
      expect(meta.ariaLabel).toBe("up 12.4%, better");
    });

    it("marks negative change as negative / red ('worse')", () => {
      const meta = computeDeltaMetadata(-8.2, "up");
      expect(meta.tone).toBe("negative");
      expect(meta.isUp).toBe(false);
      expect(meta.ariaLabel).toBe("down 8.2%, worse");
    });
  });

  describe("when goodDirection is 'down' (e.g. spend, error rate, latency)", () => {
    it("marks decrease as positive / green ('down 9.1%, better')", () => {
      const meta = computeDeltaMetadata(-9.1, "down");
      expect(meta.tone).toBe("positive");
      expect(meta.isUp).toBe(false);
      expect(meta.ariaLabel).toBe("down 9.1%, better");
    });

    it("marks increase as negative / red ('up 14.2%, worse')", () => {
      const meta = computeDeltaMetadata(14.2, "down");
      expect(meta.tone).toBe("negative");
      expect(meta.isUp).toBe(true);
      expect(meta.ariaLabel).toBe("up 14.2%, worse");
    });
  });

  describe("neutral thresholds and zero change", () => {
    it("handles exactly 0 as neutral", () => {
      const meta = computeDeltaMetadata(0, "up");
      expect(meta.tone).toBe("neutral");
      expect(meta.isNeutral).toBe(true);
      expect(meta.ariaLabel).toBe("0%, no change");
    });

    it("handles values below neutralBelow threshold", () => {
      const meta = computeDeltaMetadata(0.04, "up", 0.05);
      expect(meta.tone).toBe("neutral");
      expect(meta.isNeutral).toBe(true);
      expect(meta.ariaLabel).toContain("no change");
    });

    it("does not trigger neutral if value exceeds threshold", () => {
      const meta = computeDeltaMetadata(0.06, "up", 0.05);
      expect(meta.tone).toBe("positive");
      expect(meta.isNeutral).toBe(false);
    });
  });
});
