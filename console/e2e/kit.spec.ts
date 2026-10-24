import { expect, test } from "@playwright/test";

import path from "node:path";

test.describe("A3A — UI Kit Gallery", () => {
  test("renders gallery and saves full-page screenshots in light and dark", async ({ page }) => {
    // Navigate to the gallery page
    await page.goto("/dev/components");

    // Wait for the gallery header to be visible
    const heading = page.getByRole("heading", { name: "Component gallery" });
    await expect(heading).toBeVisible();

    // Verify key component sections are rendered
    await expect(page.getByText("1. StatusChip")).toBeVisible();
    await expect(page.getByText("2. DeltaPill")).toBeVisible();
    await expect(page.getByText("3. MetricCard")).toBeVisible();
    await expect(page.getByText("4. MeterBar")).toBeVisible();
    await expect(page.getByText("5. TrendChartCard")).toBeVisible();
    await expect(page.getByText("6. BreakdownCard")).toBeVisible();
    await expect(page.getByText("7. WeekStrip")).toBeVisible();
    await expect(page.getByText("8. TaskCard")).toBeVisible();
    await expect(page.getByText("9. InsightsPanel")).toBeVisible();
    await expect(page.getByText("10. AppSidebar")).toBeVisible();
    await expect(page.getByText("11. PageHeader")).toBeVisible();
    await expect(page.getByText("12. FilterSelect")).toBeVisible();
    await expect(page.getByText("13. ConnectAppCard")).toBeVisible();

    // Verify en-IN formatting toggle works
    const localeInButton = page.getByTestId("locale-en-in");
    await localeInButton.click();
    await expect(page.getByText("3,50,834", { exact: true })).toBeVisible();

    // Switch back to en-US
    const localeUsButton = page.getByTestId("locale-en-us");
    await localeUsButton.click();
    await expect(page.getByText("350,834", { exact: true })).toBeVisible();

    // Ensure light theme screenshot
    const lightScreenshotPath = path.resolve(__dirname, "__screenshots__/kit/gallery-light.png");
    await page.screenshot({
      path: lightScreenshotPath,
      fullPage: true,
    });

    // Toggle to dark theme
    const themeToggle = page.getByTestId("theme-toggle");
    await themeToggle.click();

    // Verify dark class applied to root
    await expect(page.locator("html")).toHaveClass(/dark/);

    // Save dark theme full-page screenshot
    const darkScreenshotPath = path.resolve(__dirname, "__screenshots__/kit/gallery-dark.png");
    await page.screenshot({
      path: darkScreenshotPath,
      fullPage: true,
    });
  });
});
