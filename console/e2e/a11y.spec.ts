import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

import { settle, shotPath } from "./support";

// WCAG 2.1 A and AA rules, which include color contrast, in both themes.
const PAGES = [
  { name: "overview", path: "/overview" },
  { name: "tools-pools", path: "/pools/tools" },
  { name: "pool-clay", path: "/pools/clay" },
  { name: "pool-claude", path: "/pools/ai/claude" },
  { name: "billing", path: "/billing" },
  { name: "login", path: "/login" },
];

async function useTheme(page: Page, theme: "light" | "dark") {
  // The Console stores the theme in a cookie and applies it before first paint.
  await page.context().addCookies([{ name: "theme_mode", value: theme, url: "http://127.0.0.1:3100" }]);
}

for (const theme of ["light", "dark"] as const) {
  test.describe(`${theme} theme`, () => {
    for (const { name, path } of PAGES) {
      test(`${name} has no WCAG AA violations`, async ({ page }) => {
        await useTheme(page, theme);
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        await expect(page.locator("html")).toHaveAttribute("data-theme-mode", theme);
        if (name === "overview") await expect(page.locator(".recharts-surface").first()).toBeVisible();
        if (name.startsWith("pool-")) {
          await expect(page.getByRole("button", { name: /^Actions for / }).first()).toBeVisible();
        }
        await settle(page);
        if (theme === "dark" && (name === "overview" || name === "pool-clay")) {
          await page.screenshot({ path: shotPath(name, 1440, "-dark"), fullPage: true });
        }

        const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
        const summary = results.violations.map((violation) => ({
          rule: violation.id,
          impact: violation.impact,
          nodes: violation.nodes
            .slice(0, 4)
            .map((node) => `${node.target.join(" ")}: ${node.failureSummary?.split("\n")[1] ?? ""}`),
        }));
        expect(summary).toEqual([]);
      });
    }
  });
}
