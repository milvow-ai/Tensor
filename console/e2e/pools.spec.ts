import { expect, type Locator, type Page, test } from "@playwright/test";

import { watchProblems } from "./support";

const row = (page: Page, id: string): Locator => page.getByTestId("account-row").filter({ hasText: id });
const toast = (page: Page, text: string): Locator => page.locator("[data-sonner-toast]", { hasText: text });

test.describe("pause and resume", () => {
  test("pausing a Clay account shows queued, then done, and the row becomes Paused", async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto("/pools/clay");
    const account = row(page, "clay-02");
    const enabled = account.getByRole("switch", { name: "Clay Launch 02 enabled" });
    await expect(enabled).toBeChecked();
    await expect(account.getByText("Active", { exact: true })).toBeVisible();

    await enabled.click();

    // 1. queued: the row shows the intent at once, locked, with a Queued chip and a toast.
    await expect(account.getByTestId("command-chip")).toHaveText(/Queued|Running/);
    await expect(account.getByText("Paused", { exact: true })).toBeVisible();
    await expect(enabled).toBeDisabled();
    await expect(toast(page, "Pause clay-02")).toBeVisible();
    // 2. done: the Farm applied it.
    await expect(toast(page, "Pause clay-02: done")).toBeVisible();
    await expect(enabled).not.toBeChecked();

    // The state is the server's, not just the browser's: it survives a reload.
    await page.reload();
    await expect(row(page, "clay-02").getByText("Paused", { exact: true })).toBeVisible();
    await expect(row(page, "clay-02").getByRole("switch", { name: "Clay Launch 02 enabled" })).not.toBeChecked();
    await expect(
      page.getByTestId("command-row").filter({ hasText: "Pause account" }).filter({ hasText: "clay-02" }),
    ).toContainText("Done");

    // Resume puts it back.
    await row(page, "clay-02").getByRole("switch", { name: "Clay Launch 02 enabled" }).click();
    await expect(toast(page, "Resume clay-02: done")).toBeVisible();
    await expect(row(page, "clay-02").getByText("Active", { exact: true })).toBeVisible();
    expect(problems()).toEqual([]);
  });

  test("the Farm can refuse: a connection test on an account that needs a login is rejected with the reason", async ({
    page,
  }) => {
    await page.goto("/pools/clay");
    const account = row(page, "clay-07");
    await expect(account.getByText("Needs login", { exact: true })).toBeVisible();

    await account.getByRole("button", { name: /Actions for/ }).click();
    await page.getByRole("menuitem", { name: "Test connection" }).click();
    await expect(account.getByTestId("command-chip")).toBeVisible();
    const rejected = toast(page, "Test clay-07: rejected");
    await expect(rejected).toBeVisible();
    await expect(rejected).toContainText("needs a login");
    // Nothing changed on the account, and the queue records the refusal.
    await expect(row(page, "clay-07").getByText("Needs login", { exact: true })).toBeVisible();
    await expect(page.getByTestId("command-row").filter({ hasText: "Test connection" }).first()).toContainText(
      "Rejected",
    );
  });
});

test("priority is edited inline and sent as set_priority", async ({ page }) => {
  await page.goto("/pools/reoon");
  const input = row(page, "reoon-02").getByRole("spinbutton", { name: "Priority of Reoon Power" });
  await input.fill("7");
  await input.press("Enter");
  await expect(toast(page, "Set priority of reoon-02 to 7: done")).toBeVisible();
  await page.reload();
  await expect(row(page, "reoon-02").getByRole("spinbutton", { name: "Priority of Reoon Power" })).toHaveValue("7");
});

test("an invalid priority is refused before anything is sent", async ({ page }) => {
  await page.goto("/pools/reoon");
  const input = row(page, "reoon-01").getByRole("spinbutton", { name: "Priority of Reoon Free" });
  await input.fill("0");
  await input.press("Enter");
  await expect(toast(page, "Priority must be a whole number")).toBeVisible();
  await expect(input).toHaveValue("1");
});

test("changing a pool strategy queues set_strategy and shows the new hint", async ({ page }) => {
  await page.goto("/pools/hunter");
  const strategy = page.getByTestId("strategy");
  await strategy.getByRole("combobox").click();
  await page.getByRole("option", { name: "Round robin" }).click();
  await expect(toast(page, "Set hunter strategy to Round robin: done")).toBeVisible();
  await expect(strategy.getByText("Rotate accounts per request.")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("strategy").getByRole("combobox")).toContainText("Round robin");
});

test("accounts are paginated and sortable, and the URL carries the state", async ({ page }) => {
  await page.goto("/pools/clay?size=5");
  await expect(page.getByText("Showing 1 to 5 of 7 accounts")).toBeVisible();
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await expect(page.getByTestId("account-row")).toHaveCount(5);

  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect(page.getByText("Showing 6 to 7 of 7 accounts")).toBeVisible();
  await expect(page.getByTestId("account-row")).toHaveCount(2);
  await expect(row(page, "clay-06")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled();

  // Sorting is the database's job: AI accounts ordered by status, ascending then descending.
  await page.goto("/pools/ai/claude");
  const ids = async () =>
    page.getByTestId("account-row").evaluateAll((rows) => rows.map((r) => r.getAttribute("data-account-id")));
  expect(await ids()).toEqual(["claude-01", "claude-02", "claude-03"]);
  await page.getByRole("button", { name: /Sort by .*status/ }).click();
  await expect(page).toHaveURL(/sort=status&dir=asc/);
  await expect.poll(ids).toEqual(["claude-01", "claude-03", "claude-02"]);
  await page.getByRole("button", { name: /Sort by .*status/ }).click();
  await expect(page).toHaveURL(/sort=status&dir=desc/);
  await expect.poll(ids).toEqual(["claude-02", "claude-03", "claude-01"]);
});

test("an AI account that needs a login shows the exact command, and its limit state counts down", async ({ page }) => {
  await page.goto("/pools/ai/claude");
  const standby = row(page, "claude-02");
  await expect(standby.getByText("Needs login", { exact: true })).toBeVisible();
  await expect(standby.getByText("farm ai login claude-02")).toBeVisible();

  const overflow = row(page, "claude-03");
  await expect(overflow.getByText("Limit reached")).toBeVisible();
  await expect(overflow.getByText(/Resets in/)).toContainText(/\d+h \d{2}m/);
});

test("a cooldown is a live countdown", async ({ page }) => {
  await page.goto("/pools/apollo");
  const cooling = row(page, "apollo-02").getByText(/Cooldown ends in/);
  await expect(cooling).toBeVisible();
  const countdown = cooling.locator("time");
  await expect(countdown).toHaveText(/^\d+m \d{2}s$/);
  const before = await countdown.innerText();
  await expect.poll(async () => countdown.innerText(), { timeout: 5000 }).not.toBe(before);
});

test("removing an account asks first and goes through the queue", async ({ page }) => {
  await page.goto("/pools/hunter");
  await row(page, "hunter-02")
    .getByRole("button", { name: /Actions for/ })
    .click();
  await page.getByRole("menuitem", { name: "Remove account" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Remove Hunter Free?");
  await page.getByRole("button", { name: "Keep account" }).click();
  await expect(row(page, "hunter-02")).toBeVisible();

  await row(page, "hunter-02")
    .getByRole("button", { name: /Actions for/ })
    .click();
  await page.getByRole("menuitem", { name: "Remove account" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove account" }).click();
  await expect(toast(page, "Remove hunter-02: done")).toBeVisible();
  await expect(row(page, "hunter-02")).toHaveCount(0);
});

test("test connection is refused while the circuit is open, with the reason", async ({ page }) => {
  await page.goto("/pools/zerobounce");
  await row(page, "zerobounce-01")
    .getByRole("button", { name: /Actions for/ })
    .click();
  await page.getByRole("menuitem", { name: "Test connection" }).click();
  const rejected = toast(page, "Test zerobounce-01: rejected");
  await expect(rejected).toBeVisible();
  await expect(rejected).toContainText("open circuit");
});
