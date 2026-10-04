import type { Page } from "@playwright/test";

import { mkdirSync } from "node:fs";
import { join } from "node:path";

/** Where screenshots go. Override with FARM_SHOTS_DIR; the default is the Farm PC's cache drive. */
export const SHOTS_DIR = process.env.FARM_SHOTS_DIR ?? "D:/dev-cache/shots/C1";

export function shotPath(name: string, width: number, suffix = ""): string {
  mkdirSync(SHOTS_DIR, { recursive: true });
  return join(SHOTS_DIR, `${name}-${width}${suffix}.png`);
}

/**
 * Collects everything that would make a page look broken to its owner: console errors, uncaught exceptions and
 * failed same-origin requests. Call `problems()` at the end of a test and expect it to be empty.
 */
export function watchProblems(page: Page, origin: string) {
  const found: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") found.push(`console.error: ${message.text()}`);
  });
  page.on("pageerror", (error) => found.push(`pageerror: ${error.message}`));
  page.on("requestfailed", (request) => {
    // Navigations that are superseded (a redirect, a refresh) are cancelled by the browser, not failures.
    if (request.failure()?.errorText !== "net::ERR_ABORTED") found.push(`requestfailed: ${request.url()}`);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === origin && response.status() >= 400) found.push(`HTTP ${response.status()}: ${url.pathname}`);
  });
  return () => found;
}

/** Waits for web fonts and a quiet moment so screenshots show the finished page. */
export async function settle(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(400);
}

/** True when the page scrolls sideways: the document is wider than the viewport. */
export async function scrollsHorizontally(
  page: Page,
): Promise<{ scrolls: boolean; scrollWidth: number; innerWidth: number }> {
  return page.evaluate(() => ({
    scrolls: document.documentElement.scrollWidth > window.innerWidth + 1,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
}
