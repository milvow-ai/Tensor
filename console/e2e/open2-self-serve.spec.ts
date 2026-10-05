import { expect, type Page, test } from "@playwright/test";

import { type Server, startServer, waitUntilReady } from "./server";
import { watchProblems } from "./support";

// OPEN2: a blank Farm. The owner starts with nothing and adds their own MCP servers and AI accounts.
// A second production server runs the fixtures in their EMPTY world (FARM_FIXTURES=empty), so the shared world of
// the main server (the one the other specs use) is never touched and there is nothing to restore.
const PORT = 3196;
let server: Server;

test.use({ baseURL: `http://127.0.0.1:${PORT}` });
test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  server = startServer(PORT, { FARM_DATA_SOURCE: "fixtures", FARM_E2E: "1", FARM_FIXTURES: "empty" });
  await waitUntilReady(server);
});

test.afterAll(async () => {
  await server.stop();
});

const toast = (page: Page, text: string) => page.locator("[data-sonner-toast]", { hasText: text });

/** Reloads until the Farm's own state (not the optimistic page) shows `check`: the command was really applied. */
async function untilTheFarmShows(page: Page, check: () => Promise<void>) {
  await expect(async () => {
    await page.reload();
    await check();
  }).toPass({ timeout: 20_000 });
}

const SCREENS = [
  { name: "overview", path: "/overview", empty: "Welcome to your Farm" },
  { name: "integrations", path: "/integrations", empty: "No integrations registered yet" },
  { name: "tool pools", path: "/pools/tools", empty: "No tool pools are registered yet." },
  { name: "AI pools", path: "/pools/ai", empty: "No AI pools are registered yet." },
  { name: "runs", path: "/runs", empty: "No runs yet" },
  { name: "billing", path: "/billing", empty: "No billing activity yet" },
  { name: "routing", path: "/routing", empty: "No routes configured" },
  { name: "memory", path: "/memory", empty: "No memory or evidence yet" },
];

for (const { name, path, empty } of SCREENS) {
  test(`a blank Farm: ${name} shows its empty state and the first action`, async ({ page, baseURL }) => {
    const problems = watchProblems(page, baseURL ?? "");
    await page.goto(path);
    await expect(page.getByText(empty, { exact: true })).toBeVisible();
    // The primary action is the same everywhere: add the first MCP server (on Integrations it opens the form).
    const action = path === "/integrations" ? "button" : "link";
    await expect(page.getByRole(action, { name: "Add your first MCP server" })).toBeVisible();
    expect(problems()).toEqual([]);
  });
}

test("Add your first MCP server enqueues add_provider and the integration appears", async ({ page, baseURL }) => {
  const problems = watchProblems(page, baseURL ?? "");
  await page.goto("/integrations");
  await page.getByRole("button", { name: "Add your first MCP server" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Add MCP Server" })).toBeVisible();
  await dialog.locator("#mcp-id").fill("fake-probe");
  await dialog.locator("#mcp-name").fill("Fake Probe Server");
  await dialog.locator("#mcp-command").fill("python");
  await dialog.locator("#mcp-args").fill("-m fake_server");

  const enqueued = page.waitForRequest(
    (request) => request.method() === "POST" && (request.postData() ?? "").includes("add_provider"),
  );
  await dialog.getByRole("button", { name: "Add Integration" }).click();
  await enqueued;

  await expect(toast(page, 'MCP server "fake-probe" registered.')).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Integration Added" })).toBeVisible();
  await expect(dialog.getByText("Next Step on Farm PC:")).toBeVisible();
  await expect(dialog.getByText("farm mcp sync")).toBeVisible();
  await dialog.getByRole("button", { name: "Done" }).click();

  await expect(page.getByText("Fake Probe Server", { exact: true })).toBeVisible();
  await expect(page.getByText("No integrations registered yet")).toHaveCount(0);
  // The Farm applied it: a fresh load of the page, with no optimistic state, lists the integration
  await untilTheFarmShows(page, () =>
    expect(page.getByText("Fake Probe Server", { exact: true })).toBeVisible({ timeout: 1_000 }),
  );
  expect(problems()).toEqual([]);
});

test("Add AI account tells you the exact `farm ai login` command and the account needs a login", async ({
  page,
  baseURL,
}) => {
  const problems = watchProblems(page, baseURL ?? "");
  await page.goto("/integrations");
  await page.getByRole("button", { name: "Add Integration" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Add Integration" })).toBeVisible();
  await dialog.getByRole("button", { name: /New AI account/ }).click();
  await expect(dialog.getByRole("heading", { name: "Add AI CLI Account" })).toBeVisible();
  await dialog.locator("#ai-acc-id").fill("claude-02");
  await dialog.locator("#ai-label").fill("Claude (second login)");

  const enqueued = page.waitForRequest(
    (request) => request.method() === "POST" && (request.postData() ?? "").includes("add_provider"),
  );
  await dialog.getByRole("button", { name: "Add Integration" }).click();
  await enqueued;

  await expect(toast(page, 'AI account "claude-02" registered.')).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Integration Added" })).toBeVisible();
  // Never a fake "active": the account waits for its login until the owner has done it
  await expect(dialog.getByText("needs_login", { exact: true })).toBeVisible();
  await expect(dialog.getByText("farm ai login claude-02")).toBeVisible();
  await dialog.getByRole("button", { name: "Done" }).click();

  // The Farm applied it: the AI pool exists, its account waits for the login and shows the exact command
  await untilTheFarmShows(page, async () => {
    await page.goto("/pools/ai/claude");
    await expect(page.getByTestId("account-row").filter({ hasText: "claude-02" })).toBeVisible({ timeout: 1_000 });
  });
  const account = page.getByTestId("account-row").filter({ hasText: "claude-02" });
  await expect(account.getByText("Needs login", { exact: true })).toBeVisible();
  await expect(account.getByText("farm ai login claude-02")).toBeVisible();
  expect(problems()).toEqual([]);
});

test("removing the integrations brings the blank state back", async ({ page, baseURL }) => {
  const problems = watchProblems(page, baseURL ?? "");
  await page.goto("/integrations");
  for (const name of ["Fake Probe Server", "CLAUDE AI Pool"]) {
    await page.getByRole("button", { name: `Remove ${name}` }).click();
    await expect(page.getByRole("heading", { name: "Remove Integration?" })).toBeVisible();
    await page.getByRole("button", { name: "Confirm Remove" }).click();
    await expect(toast(page, `Integration "${name}" removed.`)).toBeVisible();
  }
  await untilTheFarmShows(page, () =>
    expect(page.getByText("No integrations registered yet", { exact: true })).toBeVisible({ timeout: 1_000 }),
  );
  expect(problems()).toEqual([]);
});
