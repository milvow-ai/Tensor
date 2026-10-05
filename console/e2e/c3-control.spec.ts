import { expect, test } from "@playwright/test";

import { scrollsHorizontally, settle, watchProblems } from "./support";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const C3_SHOTS_DIR = "D:/dev-cache/shots/C3";

function c3Shot(name: string, width: number): string {
  mkdirSync(C3_SHOTS_DIR, { recursive: true });
  return join(C3_SHOTS_DIR, `${name}-${width}.png`);
}

test.describe("C3 — Console Control & Deploy Acceptance Tests", () => {
  test("routing reorder enqueues set_route and restores state", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/routing");

    // Header & page structure
    await expect(page.getByRole("heading", { level: 1, name: "Routing", exact: true })).toBeVisible();

    // Verify capability tabs and route card
    await expect(page.getByText("Route Order:")).toBeVisible();
    await expect(page.getByText("Who would answer now")).toBeVisible();

    // Reorder pools: find the first Move Down button
    const moveDownBtn = page.getByRole("button", { name: /Move .* down/i }).first();
    await expect(moveDownBtn).toBeVisible();
    await moveDownBtn.click();

    // Click "Save Route"
    const saveBtn = page.getByRole("button", { name: "Save Route" });
    await expect(saveBtn).toBeVisible();
    await saveBtn.click();

    // Verify toast confirms routing update
    await expect(page.locator("[data-sonner-toast]", { hasText: "Routing for" })).toBeVisible();

    // Restore original order so fixture state remains clean
    const moveUpBtn = page.getByRole("button", { name: /Move .* up/i }).nth(1);
    if (await moveUpBtn.isVisible()) {
      await moveUpBtn.click();
      await saveBtn.click();
      await expect(page.locator("[data-sonner-toast]", { hasText: "Routing for" })).toBeVisible();
    }

    expect(problems()).toEqual([]);
  });

  test("integrations form renders from schema, rejects a raw key, and enqueues add_connection", async ({
    page,
    baseURL,
  }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/integrations");

    await expect(page.getByRole("heading", { level: 1, name: "Integrations", exact: true })).toBeVisible();

    // Verify connected integrations cards are visible (Notion, GitHub, Linear, etc.)
    await expect(page.getByText("Notion Workspace").first()).toBeVisible();

    // Open "Add Integration" dialog
    const addBtn = page.getByRole("button", { name: "Add Integration" });
    await expect(addBtn).toBeVisible();
    await addBtn.click();

    // Modal opens
    await expect(page.getByRole("heading", { name: "Connect Integration" })).toBeVisible();

    // Fill ID and Name
    await page.locator("#id-input").fill("test-service");
    await page.locator("#name-input").fill("Test MCP Service");

    // Test secret rejection: enter a raw secret key
    const authInput = page.locator("#auth-ref-input");
    await authInput.fill("sk-proj-1234567890abcdef1234567890abcdef");

    // Verify rejection warning message is shown and Add button is disabled
    await expect(page.getByText("Raw secrets are forbidden in connection forms")).toBeVisible();
    const submitBtn = page.getByRole("button", { name: "Add Connection" });
    await expect(submitBtn).toBeDisabled();

    // Fix auth reference with a safe environment variable name
    await authInput.fill("env:TEST_SERVICE_API_KEY");
    await expect(page.getByText("Raw secrets are forbidden in connection forms")).not.toBeVisible();
    await expect(submitBtn).toBeEnabled();

    // Submit the form
    await submitBtn.click();

    // Verify success toast
    await expect(
      page.locator("[data-sonner-toast]", { hasText: 'Connection "test-service-01" queued.' }),
    ).toBeVisible();

    // Verify post-save CLI instruction card is rendered
    await expect(page.getByText("Next Step: Store Secret Locally")).toBeVisible();
    await expect(page.getByText("farm set-secret TEST_SERVICE_API_KEY")).toBeVisible();

    // Close the dialog
    await page.getByRole("button", { name: "Done" }).click();
    await expect(page.getByRole("heading", { name: "Connect Integration" })).not.toBeVisible();

    expect(problems()).toEqual([]);
  });

  test("policies page allows budget editing and restores state", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/policies");

    await expect(page.getByRole("heading", { level: 1, name: "Policies & Budgets", exact: true })).toBeVisible();

    // Global cap card
    await expect(page.getByText("Global Spending Cap", { exact: true })).toBeVisible();
    await expect(page.getByText("Alert Thresholds", { exact: true })).toBeVisible();
    await expect(page.getByText("Audit Log", { exact: true })).toBeVisible();

    // Click Edit Cap
    const editCapBtn = page.getByRole("button", { name: "Edit Cap" });
    await expect(editCapBtn).toBeVisible();
    await editCapBtn.click();

    // Fill new cap amount
    const capInput = page.locator('input[type="number"]').first();
    await expect(capInput).toBeVisible();
    await capInput.fill("9500");

    // Save global cap
    const saveCapBtn = page.getByRole("button", { name: "Save" }).first();
    await saveCapBtn.click();
    await expect(
      page.locator("[data-sonner-toast]", { hasText: "Global Farm spending budget updated." }),
    ).toBeVisible();
    await expect(page.getByText("$9,500")).toBeVisible();

    // Restore to 7000 to keep fixture state clean
    await page.getByRole("button", { name: "Edit Cap" }).click();
    await capInput.fill("7000");
    await saveCapBtn.click();
    await expect(
      page.locator("[data-sonner-toast]", { hasText: "Global Farm spending budget updated." }),
    ).toBeVisible();
    await expect(page.getByText("$7,000")).toBeVisible();

    expect(problems()).toEqual([]);
  });

  test("memory page displays facts with freshness badges and evidence viewer", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/memory");

    await expect(page.getByRole("heading", { level: 1, name: "Memory & Evidence", exact: true })).toBeVisible();

    // Freshness badges in facts table
    await expect(page.getByText("Knowledge Base Facts")).toBeVisible();
    await expect(page.getByText("Fresh").first()).toBeVisible();
    await expect(page.getByText("Stale").first()).toBeVisible();
    await expect(page.getByText("Expired").first()).toBeVisible();

    // Switch to Evidence tab
    const evidenceTab = page.getByRole("tab", { name: /Evidence/i });
    await expect(evidenceTab).toBeVisible();
    await evidenceTab.click();

    // Evidence artifacts table
    await expect(page.getByText("Captured Evidence & Artifacts")).toBeVisible();
    await expect(page.getByText("sha256:").first()).toBeVisible();

    expect(problems()).toEqual([]);
  });

  test("settings page renders engine status, owner identity, and timezone", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/settings");

    await expect(page.getByRole("heading", { level: 1, name: "Settings", exact: true })).toBeVisible();

    // Farm engine runtime status card
    await expect(page.getByText("Farm Engine Runtime")).toBeVisible();
    await expect(page.getByText("Healthy")).toBeVisible();

    // Owner and notification sections
    await expect(page.getByText("Owner Identity & Access")).toBeVisible();
    await expect(page.getByText("Telegram Alerts")).toBeVisible();

    expect(problems()).toEqual([]);
  });

  test("no Radix Primitive.button or dev errors occur across C3 surfaces", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");

    const paths = [
      "/overview",
      "/billing",
      "/routing",
      "/integrations",
      "/memory",
      "/policies",
      "/settings",
      "/pools/ai",
    ];

    for (const path of paths) {
      await page.goto(path);
      await settle(page);
      const errors = problems().filter(
        (p) =>
          p.includes("Primitive.button") ||
          p.includes("failed to slot onto its children") ||
          p.includes("Expected a single React element"),
      );
      expect(errors, `Radix slot error detected on ${path}`).toEqual([]);
    }
  });

  test("generates C3 screenshots at 1440px and 390px in D:/dev-cache/shots/C3", async ({ page }) => {
    const screens = [
      { name: "routing", path: "/routing" },
      { name: "memory", path: "/memory" },
      { name: "integrations", path: "/integrations" },
      { name: "policies", path: "/policies" },
      { name: "settings", path: "/settings" },
      { name: "ai-pools", path: "/pools/ai" },
    ];

    // 1440px Desktop
    await page.setViewportSize({ width: 1440, height: 900 });
    for (const screen of screens) {
      await page.goto(screen.path);
      await settle(page);
      expect((await scrollsHorizontally(page)).scrolls).toBe(false);
      await page.screenshot({ path: c3Shot(screen.name, 1440), fullPage: true });
    }

    // 390px Mobile
    await page.setViewportSize({ width: 390, height: 844 });
    for (const screen of screens) {
      await page.goto(screen.path);
      await settle(page);
      expect((await scrollsHorizontally(page)).scrolls).toBe(false);
      await page.screenshot({ path: c3Shot(screen.name, 390), fullPage: true });
    }
  });
});
