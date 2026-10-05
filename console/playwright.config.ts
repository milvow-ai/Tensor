import { defineConfig, devices } from "@playwright/test";

// FARM_E2E_PORT lets the suite run while something else (the live Console preview) holds 3100.
const PORT = Number(process.env.FARM_E2E_PORT ?? 3100);

/**
 * End-to-end suite: the production build (`next start`) in fixtures mode.
 *
 * `next start` always runs with NODE_ENV=production, which the fixtures guard refuses, so the run sets FARM_E2E=1.
 * The in-memory fixture world is shared by the whole run, so the projects are serial and ordered: "screens" looks at
 * every page in its initial state, then "behaviour" changes things through the command queue.
 *
 * Run `pnpm check` (it builds) before this; `pnpm test:e2e` uses the existing `.next` build.
 * Browsers live under PLAYWRIGHT_BROWSERS_PATH (D:\dev-cache\ms-playwright on the Farm PC).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "screens",
      testMatch: /(pages|a11y)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "behaviour",
      testMatch: /(overview|pools|add-account|guard|auth|c2-billing-runs|c3-control|open2-self-serve)\.spec\.ts/,
      dependencies: ["screens"],
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: `pnpm exec next start -p ${PORT} -H 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/login`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { FARM_DATA_SOURCE: "fixtures", FARM_E2E: "1" },
    stdout: "pipe",
    stderr: "pipe",
  },
});
