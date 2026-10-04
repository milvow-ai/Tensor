import { expect, test } from "@playwright/test";

import { startServer } from "./server";

// The fixtures guard is the Console's protection against a deployed instance showing invented data.
// It has to hold in the real production server, so this starts one without the e2e opt-in.
test("a production server refuses to start on fixtures data", async () => {
  const server = startServer(3199, { FARM_DATA_SOURCE: "fixtures", FARM_E2E: undefined });
  const timeout = new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), 40_000));
  const result = await Promise.race([server.exited, timeout]);
  await server.stop();

  expect(server.output()).toContain("not allowed when NODE_ENV=production");
  expect(result, "the server must exit instead of serving").toBe(1);
});

test("a server with an unknown data source also refuses to start", async () => {
  const server = startServer(3198, { FARM_DATA_SOURCE: "sqlite", FARM_E2E: undefined });
  const timeout = new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), 40_000));
  const result = await Promise.race([server.exited, timeout]);
  await server.stop();

  expect(server.output()).toContain('FARM_DATA_SOURCE must be "supabase" or "fixtures"');
  expect(result).toBe(1);
});
