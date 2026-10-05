import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

import { scrollsHorizontally, settle, watchProblems } from "./support";

const C2_SHOTS_DIR = "D:/dev-cache/shots/C2";

function c2Shot(name: string, width: number): string {
  mkdirSync(C2_SHOTS_DIR, { recursive: true });
  return join(C2_SHOTS_DIR, `${name}-${width}.png`);
}

test.describe("C2 — Billing & Runs acceptance tests", () => {
  test("billing renders KPIs, forecast over-budget flag, budgets, and renewals from fixtures", async ({
    page,
    baseURL,
  }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/billing");

    // Header & page structure
    await expect(page.getByRole("heading", { level: 1, name: "Billing", exact: true })).toBeVisible();

    // 1. KPI row & forecast flag
    // In fixtures, spend is $7,500 against $7,000 global budget; forecast is $7,750 > budget.
    await expect(page.getByText("Month-to-date spend")).toBeVisible();
    await expect(page.getByText("$7,500", { exact: true })).toBeVisible();
    await expect(page.getByText("of $7,000 budget")).toBeVisible();

    // Month-end forecast & hover method
    await expect(page.getByText("Month-end forecast")).toBeVisible();
    await expect(page.getByText("$7,750", { exact: true })).toBeVisible();
    await expect(page.getByText("Method: Spend ÷ elapsed days × days in month")).toBeVisible();

    // Over-budget flag
    const forecastFlag = page.getByTestId("forecast-flag");
    await expect(forecastFlag).toBeVisible();
    await expect(forecastFlag).toContainText("Forecast exceeds monthly budget!");

    // Paid & idle accounts KPIs
    await expect(page.getByText("Paid accounts", { exact: true })).toBeVisible();
    await expect(page.getByText("Idle paid accounts", { exact: true })).toBeVisible();

    // 2. Budgets table & inline editing
    const globalRow = page.getByTestId("budget-row-global");
    await expect(globalRow).toBeVisible();
    await expect(globalRow).toContainText("Global");
    await expect(globalRow).toContainText("$7,000");

    // Validation test: negative number
    await page.getByTestId("edit-budget-btn-global").click();
    const capInput = page.getByTestId("budget-input-global");
    await expect(capInput).toBeVisible();
    await capInput.fill("-100");
    await page.getByTestId("save-budget-btn-global").click();
    await expect(page.getByTestId("budget-error-global")).toContainText("Budget cannot be negative");

    // Valid budget update: set to $8,500
    await capInput.fill("8500");
    await page.getByTestId("save-budget-btn-global").click();
    await expect(page.locator("[data-sonner-toast]", { hasText: "done" })).toBeVisible();
    await expect(globalRow).toContainText("$8,500");

    // Hard stop toggle on budget row
    const hardStopSwitch = globalRow.getByRole("switch");
    await expect(hardStopSwitch).toBeChecked();
    await hardStopSwitch.click();
    await expect(page.locator("[data-sonner-toast]", { hasText: "done" })).toBeVisible();

    // 3. Renewal calendar
    await expect(page.getByRole("heading", { name: "Renewal calendar (next 45 days)" })).toBeVisible();
    // Clay renewal has low usage (<20%)
    await expect(page.getByText("Usage: 12% · Consider cancelling")).toBeVisible();
    // Human task link exists
    await expect(page.getByRole("link", { name: /Open cancellation task/i }).first()).toBeVisible();

    // 4. Idle paid accounts card & Pause action
    await expect(page.getByRole("heading", { name: "Idle paid accounts (14+ days)" })).toBeVisible();
    const clay07Row = page.getByTestId("idle-account-clay-07");
    await expect(clay07Row).toContainText("Clay Pro");
    await expect(clay07Row).toContainText("Needs Login");
    // Pause an idle account that is Active, then resume it from its pool page: the fixture state is shared by
    // every spec in this run, so this test must leave it exactly as it found it.
    const activeIdle = page.locator('[data-testid^="idle-account-"]').filter({ hasText: "Active (Idle)" }).first();
    await expect(activeIdle).toBeVisible();
    const idleId = ((await activeIdle.getAttribute("data-testid")) ?? "").replace("idle-account-", "");
    expect(idleId).toMatch(/^[a-z0-9]+-\d+$/);
    await activeIdle.getByRole("button", { name: "Pause account" }).click();
    await expect(page.locator("[data-sonner-toast]", { hasText: `Pause ${idleId}: done` })).toBeVisible();

    await page.goto(`/pools/${idleId.replace(/-\d+$/, "")}`);
    const poolRow = page.getByTestId("account-row").filter({ hasText: idleId });
    await expect(poolRow.getByText("Paused", { exact: true })).toBeVisible();
    await poolRow.getByRole("switch").click();
    await expect(page.locator("[data-sonner-toast]", { hasText: `Resume ${idleId}: done` })).toBeVisible();
    await expect(poolRow.getByText("Active", { exact: true })).toBeVisible();

    expect(problems()).toEqual([]);
  });

  test("runs page filter 'failures only' works and URL-syncs, and capability filter works", async ({
    page,
    baseURL,
  }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/runs");

    await expect(page.getByRole("heading", { level: 1, name: "Runs", exact: true })).toBeVisible();

    // In initial state, multiple runs are shown, including Succeeded
    const table = page.locator("table");
    await expect(table).toBeVisible();
    await expect(page.getByText("Succeeded").first()).toBeVisible();

    // Filter "Failures only"
    const failuresSwitch = page.getByTestId("failures-only-switch");
    await expect(failuresSwitch).not.toBeChecked();
    await failuresSwitch.click();

    // URL syncs
    await expect(page).toHaveURL(/failures_only=true/);

    // Only failed runs visible
    await expect(page.getByText("Succeeded")).toHaveCount(0);
    await expect(page.getByText("Failed").first()).toBeVisible();

    // Toggle failures off
    await failuresSwitch.click();
    await expect(page).not.toHaveURL(/failures_only=true/);
    await expect(page.getByText("Succeeded").first()).toBeVisible();

    // Test capability filter
    const capSelect = page.getByRole("combobox", { name: "Filter by capability" });
    await capSelect.selectOption("verify_email");
    await expect(page).toHaveURL(/capability=verify_email/);
    await expect(page.getByRole("cell", { name: "verify_email" }).first()).toBeVisible();
    await expect(page.getByRole("cell", { name: "enrich_company" })).toHaveCount(0);

    expect(problems()).toEqual([]);
  });

  test("run detail drawer and page show the fallback step with its reason", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/runs");

    // Click on run 00000000-0000-0000-0000-000000000002 which has fallback event in fixtures
    const runRow = page.getByTestId("run-row-00000000-0000-0000-0000-000000000002");
    await expect(runRow).toBeVisible();
    await runRow.click();

    // Drawer opens
    const fallbackBlock = page.getByTestId("fallback-reason-block");
    await expect(fallbackBlock).toBeVisible();
    await expect(fallbackBlock).toContainText("Fallback Triggered");
    await expect(fallbackBlock).toContainText(
      "Rate limited on primary connection clay-01 (429 Too Many Requests), falling back to secondary connection clay-02",
    );
    await expect(fallbackBlock).toContainText("clay-01");
    await expect(fallbackBlock).toContainText("clay-02");

    // Stepper shows latency badge and steps
    await expect(page.getByTestId("event-step-fallback")).toBeVisible();
    await expect(page.getByText("+120ms")).toBeVisible();

    // Result envelope is rendered and redacted
    await expect(page.getByText("Result envelope")).toBeVisible();
    await expect(page.getByText("[REDACTED]")).toBeVisible();

    // Test dedicated run detail full page
    await page.goto("/runs/00000000-0000-0000-0000-000000000002");
    await expect(page.getByRole("heading", { level: 1, name: "enrich_company" })).toBeVisible();
    const pageFallback = page.getByTestId("fallback-reason-block");
    await expect(pageFallback).toBeVisible();
    await expect(pageFallback).toContainText(
      "Rate limited on primary connection clay-01 (429 Too Many Requests), falling back to secondary connection clay-02",
    );
    await expect(page.getByText("Evidence on the Farm PC")).toBeVisible();

    expect(problems()).toEqual([]);
  });

  test("generates C2 screenshots at 1440px and 390px in D:/dev-cache/shots/C2", async ({ page }) => {
    // 1440px Desktop
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.goto("/billing");
    await settle(page);
    expect((await scrollsHorizontally(page)).scrolls).toBe(false);
    await page.screenshot({ path: c2Shot("billing", 1440), fullPage: true });

    await page.goto("/runs");
    await settle(page);
    expect((await scrollsHorizontally(page)).scrolls).toBe(false);
    await page.screenshot({ path: c2Shot("runs", 1440), fullPage: true });

    await page.goto("/runs/00000000-0000-0000-0000-000000000002");
    await settle(page);
    expect((await scrollsHorizontally(page)).scrolls).toBe(false);
    await page.screenshot({ path: c2Shot("run-detail", 1440), fullPage: true });

    // 390px Mobile
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("/billing");
    await settle(page);
    expect((await scrollsHorizontally(page)).scrolls).toBe(false);
    await page.screenshot({ path: c2Shot("billing", 390), fullPage: true });

    await page.goto("/runs");
    await settle(page);
    expect((await scrollsHorizontally(page)).scrolls).toBe(false);
    await page.screenshot({ path: c2Shot("runs", 390), fullPage: true });

    await page.goto("/runs/00000000-0000-0000-0000-000000000002");
    await settle(page);
    expect((await scrollsHorizontally(page)).scrolls).toBe(false);
    await page.screenshot({ path: c2Shot("run-detail", 390), fullPage: true });
  });
});
