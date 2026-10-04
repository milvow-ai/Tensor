import { expect, test } from "@playwright/test";

import { type Server, startServer, waitUntilReady } from "./server";

// Supabase mode, signed out. Nothing here needs the network: a visitor with no session is turned away by the proxy
// before any data is read, and the sign-in form validates locally before it would call Supabase.
let server: Server;

test.beforeAll(async () => {
  server = startServer(3197, {
    FARM_DATA_SOURCE: "supabase",
    FARM_E2E: undefined,
    // Placeholders so the form renders as configured even without .env.local; nothing here is ever contacted.
    NEXT_PUBLIC_SUPABASE_URL: "http://127.0.0.1:9",
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_placeholder",
  });
  await waitUntilReady(server);
});

test.afterAll(async () => {
  await server.stop();
});

test("signed-out visitors are sent to /login and nothing of the Console is served", async () => {
  for (const path of ["/overview", "/pools/clay", "/pools/ai/claude", "/runs/abc"]) {
    const response = await fetch(`${server.url}${path}`, { redirect: "manual" });
    expect(response.status, path).toBe(307);
    expect(response.headers.get("location"), path).toBe(`/login?next=${encodeURIComponent(path)}`);
    expect(await response.text(), path).not.toContain("Harness Farm");
  }
});

test("a server action cannot be used without a session", async () => {
  const response = await fetch(`${server.url}/overview`, {
    method: "POST",
    redirect: "manual",
    headers: { "Next-Action": "00", "content-type": "text/plain;charset=UTF-8" },
    body: "[]",
  });
  expect(response.status).toBe(307);
  expect(response.headers.get("location")).toContain("/login");
});

test("the login page validates the email locally and the fixtures notice is absent", async ({ page }) => {
  await page.goto(`${server.url}/login`);
  await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
  await expect(page.getByText("Sign-in is skipped in fixtures mode")).toHaveCount(0);

  await page.getByRole("button", { name: "Send magic link" }).click();
  await expect(page.getByText("Enter your email")).toBeVisible();
  await page.getByLabel("Email").fill("not-an-email");
  await page.getByRole("button", { name: "Send magic link" }).click();
  await expect(page.getByText("That does not look like an email address")).toBeVisible();
});

test("an invalid magic link lands on /login with an explanation", async ({ page }) => {
  await page.goto(`${server.url}/auth/callback`);
  await expect(page).toHaveURL(/\/login\?error=link$/);
  await expect(page.getByText("That sign-in link is invalid or has expired")).toBeVisible();
});

test("the unauthorized page explains itself and offers sign-out", async ({ page }) => {
  await page.goto(`${server.url}/unauthorized`);
  await expect(page.getByRole("heading", { level: 1, name: "This Console is private" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out and use another email" })).toBeVisible();
});

test("signing out returns to /login", async () => {
  const response = await fetch(`${server.url}/auth/signout`, { method: "POST", redirect: "manual" });
  expect(response.status).toBe(303);
  expect(response.headers.get("location")).toBe("/login");
});
