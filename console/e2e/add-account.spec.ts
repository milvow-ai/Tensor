import { expect, type Page, test } from "@playwright/test";

import { watchProblems } from "./support";

const toast = (page: Page, text: string) => page.locator("[data-sonner-toast]", { hasText: text });

async function openAddDialog(page: Page, pool: string) {
  await page.goto(`/pools/${pool}`);
  await page.getByRole("button", { name: "Add account" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  return dialog;
}

test("the add-account form refuses a pasted secret and sends nothing", async ({ page }) => {
  const dialog = await openAddDialog(page, "hunter");
  await dialog.getByLabel("Label").fill("Hunter Team");
  await dialog.getByLabel("Plan name").fill("Team");

  const auth = dialog.getByLabel("Auth reference (env-var name)");
  for (const secret of [
    "sk-live-4f9a8b7c6d5e4f3a2b1c",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "eyJhbGciOiJIUzI1NiJ9.payload.sig",
  ]) {
    await auth.fill(secret);
    await expect(dialog.getByText("This looks like a secret, not a name")).toBeVisible();
  }
  // A plain lower-case word is not a secret, but it is not an env-var name either.
  await auth.fill("my key");
  await expect(dialog.getByText("Use UPPER_SNAKE_CASE")).toBeVisible();

  await auth.fill("sk-live-4f9a8b7c6d5e4f3a2b1c");
  await dialog.getByRole("button", { name: "Queue account" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("This looks like a secret, not a name")).toBeVisible();
  await expect(toast(page, "Add account")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByTestId("command-row").filter({ hasText: "Add account" })).toHaveCount(0);
});

test("a valid account is queued, shows as pending, is added by the Farm and appears in the table", async ({
  page,
  baseURL,
}) => {
  const problems = watchProblems(page, baseURL ?? "");
  const dialog = await openAddDialog(page, "hunter");
  await expect(dialog.getByLabel("Account id")).toHaveValue("hunter-03");
  await dialog.getByLabel("Label").fill("Hunter Team");
  await dialog.getByLabel("Auth reference (env-var name)").fill("HUNTER_KEY_03");
  await dialog.getByLabel("Plan name").fill("Team");
  await dialog.getByLabel("Price / month (USD)").fill("99");
  await dialog.getByLabel("Billing day").fill("12");
  await dialog.getByLabel("Unit", { exact: true }).fill("searches");
  await dialog.getByLabel("Limit").fill("2500");
  await dialog.getByRole("button", { name: "Queue account" }).click();

  // Queued: the dialog closes, a toast and a placeholder row say it is waiting for the Farm.
  await expect(dialog).toBeHidden();
  await expect(toast(page, "Add account hunter-03")).toBeVisible();
  await expect(page.getByTestId("adding-row")).toContainText("hunter-03");
  // The command is in the queue list.
  const queued = page.getByTestId("command-row").filter({ hasText: "hunter-03" });
  await expect(queued).toContainText("Add account");

  // Done: the real row replaces the placeholder with what was entered.
  await expect(toast(page, "Add account hunter-03: done")).toBeVisible();
  const added = page.getByTestId("account-row").filter({ hasText: "hunter-03" });
  await expect(added).toBeVisible();
  await expect(added).toContainText("Hunter Team");
  await expect(added).toContainText("env:HUNTER_KEY_03");
  await expect(added).toContainText("Team");
  await expect(added).toContainText("$99/mo");
  await expect(added).toContainText("0 / 2,500 searches");
  await expect(page.getByTestId("adding-row")).toHaveCount(0);
  await expect(queued).toContainText("Done");
  expect(problems()).toEqual([]);
});

test("the Farm refuses a duplicate id and no second row appears", async ({ page }) => {
  const dialog = await openAddDialog(page, "reoon");
  await dialog.getByLabel("Account id").fill("reoon-01");
  await dialog.getByLabel("Label").fill("Duplicate");
  await dialog.getByLabel("Auth reference (env-var name)").fill("REOON_KEY_09");
  await dialog.getByLabel("Plan name").fill("Free");
  await dialog.getByLabel("Unit", { exact: true }).fill("credits");
  await dialog.getByRole("button", { name: "Queue account" }).click();

  const rejected = toast(page, "Add account reoon-01: rejected");
  await expect(rejected).toBeVisible();
  await expect(rejected).toContainText("already exists");
  await expect(page.getByTestId("account-row").filter({ hasText: "reoon-01" })).toHaveCount(1);
  await expect(page.getByTestId("adding-row")).toHaveCount(0);
});

test("adding an AI account asks for the CLI and models, and tells you the login command", async ({ page }) => {
  await page.goto("/pools/ai");
  await page.getByRole("button", { name: "Add AI account" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("CLI type").selectOption("codex");
  await expect(dialog.getByLabel("Models")).toHaveValue("gpt-5-codex, gpt-5");
  await dialog.getByLabel("Account id").fill("codex-02");
  await dialog.getByLabel("Label").fill("Codex (second seat)");
  await expect(dialog.getByText("farm ai login codex-02")).toBeVisible();
  await dialog.getByLabel("Models").fill("gpt 5");
  await expect(dialog.getByText("Comma-separated model names")).toBeVisible();
  await dialog.getByLabel("Models").fill("gpt-5-codex");
  await dialog.getByRole("button", { name: "Queue account" }).click();

  await expect(toast(page, "Add AI account codex-02: done")).toBeVisible();
  await page.goto("/pools/ai/codex");
  const added = page.getByTestId("account-row").filter({ hasText: "codex-02" });
  await expect(added).toContainText("Codex (second seat)");
  await expect(added).toContainText("cli:codex-02");
});
