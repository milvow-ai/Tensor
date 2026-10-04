import { expect, test } from "@playwright/test";

import { scrollsHorizontally, settle, shotPath, watchProblems } from "./support";

interface Screen {
  name: string;
  path: string;
  heading: string;
}

// Every page of the Console: the nav pages, the pool detail pages, and the sign-in states.
const SCREENS: Screen[] = [
  { name: "overview", path: "/overview", heading: "Overview" },
  { name: "tools-pools", path: "/pools/tools", heading: "Tools & Pools" },
  { name: "pool-clay", path: "/pools/clay", heading: "Clay" },
  { name: "pool-zerobounce", path: "/pools/zerobounce", heading: "ZeroBounce" },
  { name: "ai-pools", path: "/pools/ai", heading: "AI Pools" },
  { name: "pool-claude", path: "/pools/ai/claude", heading: "Claude" },
  { name: "runs", path: "/runs", heading: "Runs" },
  { name: "routing", path: "/routing", heading: "Routing" },
  { name: "policies", path: "/policies", heading: "Policies & Budgets" },
  { name: "billing", path: "/billing", heading: "Billing" },
  { name: "memory", path: "/memory", heading: "Memory & Evidence" },
  { name: "integrations", path: "/integrations", heading: "Integrations" },
  { name: "settings", path: "/settings", heading: "Settings" },
];

const VIEWPORTS = [
  { width: 1440, height: 900, mobile: false },
  { width: 390, height: 844, mobile: true },
];

for (const viewport of VIEWPORTS) {
  test.describe(`${viewport.width}px wide`, () => {
    test.use({
      viewport: { width: viewport.width, height: viewport.height },
      isMobile: viewport.mobile,
      hasTouch: viewport.mobile,
    });

    for (const screen of SCREENS) {
      test(`${screen.name} renders cleanly and fits the screen`, async ({ page, baseURL }) => {
        const problems = watchProblems(page, baseURL ?? "");
        await page.goto(screen.path);
        await expect(page.getByRole("heading", { level: 1, name: screen.heading, exact: true })).toBeVisible();
        if (screen.name === "overview") await expect(page.locator(".recharts-surface").first()).toBeVisible();
        if (screen.name.startsWith("pool-")) {
          await expect(page.getByTestId("accounts-table")).toBeVisible();
          // The row actions menu loads right after hydration.
          await expect(page.getByRole("button", { name: /^Actions for / }).first()).toBeVisible();
        }
        await settle(page);

        const overflow = await scrollsHorizontally(page);
        expect(overflow.scrolls, `page is ${overflow.scrollWidth}px wide in a ${overflow.innerWidth}px viewport`).toBe(
          false,
        );
        await page.screenshot({ path: shotPath(screen.name, viewport.width), fullPage: true });
        expect(problems()).toEqual([]);
      });
    }

    test("a run opens from Overview", async ({ page, baseURL }) => {
      const problems = watchProblems(page, baseURL ?? "");
      await page.goto("/overview");
      await page.locator('a[href^="/runs/"]:visible').first().click();
      await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);
      await expect(page.getByRole("heading", { level: 2, name: "Summary" })).toBeVisible();
      await settle(page);
      expect((await scrollsHorizontally(page)).scrolls).toBe(false);
      await page.screenshot({ path: shotPath("run-detail", viewport.width), fullPage: true });
      expect(problems()).toEqual([]);
    });

    test("sign-in states render (fixtures mode skips auth)", async ({ page, baseURL }) => {
      const problems = watchProblems(page, baseURL ?? "");
      await page.goto("/login");
      await expect(page.getByText("Sign-in is skipped in fixtures mode")).toBeVisible();
      await settle(page);
      await page.screenshot({ path: shotPath("login", viewport.width), fullPage: true });

      await page.goto("/unauthorized");
      await expect(page.getByText("This Console is private")).toBeVisible();
      await settle(page);
      await page.screenshot({ path: shotPath("unauthorized", viewport.width), fullPage: true });
      expect(problems()).toEqual([]);
    });

    test("an unknown page shows the not-found state", async ({ page }) => {
      // The page streams behind a loading state, so the status line is already 200 when notFound() fires.
      await page.goto("/pools/does-not-exist");
      await expect(page.getByRole("heading", { name: "This page does not exist" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: shotPath("not-found", viewport.width), fullPage: true });
    });
  });
}

test("every nav entry opens its page", async ({ page }) => {
  await page.goto("/overview");
  const nav = page.getByRole("navigation").or(page.locator('[data-slot="sidebar"]'));
  const links = [
    ["Tools & Pools", "/pools/tools"],
    ["AI Pools", "/pools/ai"],
    ["Runs", "/runs"],
    ["Routing", "/routing"],
    ["Policies & Budgets", "/policies"],
    ["Billing", "/billing"],
    ["Memory & Evidence", "/memory"],
    ["Integrations", "/integrations"],
    ["Settings", "/settings"],
    ["Overview", "/overview"],
  ] as const;
  for (const [label, path] of links) {
    await nav.first().getByRole("link", { name: label, exact: false }).first().click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
  }
});
