import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("node:fs/promises", () => ({ readFile: vi.fn() }));

import { readTeamReports } from "../team-reporting";
import { readFile } from "node:fs/promises";

const data = {
  version: 1,
  instance: "owner",
  observed_at: "2026-11-11T12:00:00Z",
  timezone: "UTC",
  window_start: "2026-11-01T00:00:00Z",
  ai_accounts: 2,
  mcp_accounts: 3,
  other_accounts: 0,
  jobs: { succeeded: 4 },
  unpriced_jobs: 1,
  missing_metering_jobs: 1,
  recorded_cost_usd: 0,
  usage: [],
  usage_truncated: false,
};

describe("team reporting", () => {
  beforeEach(() => {
    vi.stubEnv("FARM_TEAM_REPORTS_FILE", "/admin/reports.json");
    vi.stubEnv("REPORT_TOKEN", "private-sentinel");
    vi.mocked(readFile).mockResolvedValue(
      JSON.stringify([{ name: "owner", endpoint: "https://farm.example/reports/usage", token_env: "REPORT_TOKEN" }]),
    );
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });
  it("returns validated counters without credential material", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));
    const result = await readTeamReports();
    expect(result[0].snapshot?.ai_accounts).toBe(2);
    expect(JSON.stringify(result)).not.toContain("private-sentinel");
  });
  it("keeps an unavailable profile unknown and rejects unexpected private fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...data, prompt: "private" }))));
    const result = await readTeamReports();
    expect(result[0].snapshot).toBeNull();
    expect(result[0].error).toContain("unknown");
    expect(JSON.stringify(result)).not.toContain("private-sentinel");
  });
  it("rejects public HTTP before sending a credential", async () => {
    vi.mocked(readFile).mockResolvedValue(
      JSON.stringify([{ name: "owner", endpoint: "http://public.example/reports/usage", token_env: "REPORT_TOKEN" }]),
    );
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await expect(readTeamReports()).rejects.toThrow("configuration");
    expect(fetcher).not.toHaveBeenCalled();
  });
});
