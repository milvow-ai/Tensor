import { expect, test } from "@playwright/test";

import { watchProblems } from "./support";

test("acknowledging an alert goes through the command queue and clears it", async ({ page, baseURL }) => {
  const problems = watchProblems(page, baseURL ?? "");
  await page.goto("/overview");

  const alerts = page.getByTestId("alerts-card");
  const openBefore = Number((await alerts.getByTestId("alerts-open").innerText()).split(" ")[0]);
  const first = alerts.getByRole("listitem").first();
  const message = (await first.locator("p").first().innerText()).trim();

  await first.getByRole("button", { name: "Acknowledge" }).click();
  // Optimistic feedback first: the alert shows it is queued and its button locks.
  await expect(first.getByTestId("command-chip")).toContainText(/Queued|Running/);
  await expect(first.getByRole("button", { name: "Acknowledge" })).toBeDisabled();
  await expect(page.locator("[data-sonner-toast]", { hasText: "done" })).toBeVisible();

  // Then the Farm's answer: the alert left the open list and the count went down.
  await expect(alerts.getByText(message, { exact: true })).toHaveCount(0);
  await expect(alerts.getByTestId("alerts-open")).toHaveText(`${openBefore - 1} open`);
  expect(problems()).toEqual([]);
});
