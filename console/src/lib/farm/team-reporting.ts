import "server-only";

import { z } from "zod";

import { readFile } from "node:fs/promises";

const count = z.number().int().nonnegative();
const metric = z.number().finite().nonnegative();
export const teamSnapshot = z
  .object({
    version: z.literal(1),
    instance: z.string().min(1).max(64),
    observed_at: z.iso.datetime({ offset: true }),
    timezone: z.string().max(80),
    window_start: z.iso.datetime({ offset: true }),
    ai_accounts: count,
    mcp_accounts: count,
    other_accounts: count,
    jobs: z.record(z.string().max(32), count),
    unpriced_jobs: count,
    missing_metering_jobs: count,
    recorded_cost_usd: metric,
    usage: z
      .array(z.object({ provider: z.string().max(128), unit: z.string().max(64), amount: metric }).strict())
      .max(200),
    usage_truncated: z.boolean(),
  })
  .strict();

const endpoint = z
  .string()
  .url()
  .refine((raw) => {
    const url = new URL(raw);
    return (
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      url.pathname.endsWith("/reports/usage") &&
      (url.protocol === "https:" ||
        (url.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)))
    );
  });
const sources = z
  .array(
    z
      .object({
        name: z.string().regex(/^[a-z][a-z0-9-]{0,19}$/),
        endpoint,
        token_env: z.string().regex(/^[A-Z][A-Z0-9_]{2,127}$/),
      })
      .strict(),
  )
  .max(16);

export type TeamReport = {
  name: string;
  snapshot: z.infer<typeof teamSnapshot> | null;
  error: string | null;
};

/** Only administrator-owned configuration supplies URLs and credential references. */
export async function readTeamReports(): Promise<TeamReport[]> {
  const path = process.env.FARM_TEAM_REPORTS_FILE;
  if (!path) return [];
  let configured: z.infer<typeof sources>;
  try {
    configured = sources.parse(JSON.parse(await readFile(path, "utf8")));
    if (
      new Set(configured.map((s) => s.name)).size !== configured.length ||
      new Set(configured.map((s) => s.endpoint)).size !== configured.length
    )
      throw new Error("duplicate");
  } catch {
    throw new Error("Team reporting configuration is invalid or unreadable.");
  }
  return Promise.all(
    configured.map(async (source): Promise<TeamReport> => {
      const token = process.env[source.token_env];
      if (!token) return { name: source.name, snapshot: null, error: "Reporting credential missing" };
      try {
        const response = await fetch(source.endpoint, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          redirect: "error",
          signal: AbortSignal.timeout(4000),
        });
        if (!response.ok) throw new Error("unavailable");
        const length = Number(response.headers.get("content-length") ?? 0);
        if (length > 262144) throw new Error("oversize");
        const reader = response.body?.getReader();
        if (!reader) throw new Error("empty");
        const chunks: Uint8Array[] = [];
        let size = 0;
        try {
          let part = await reader.read();
          while (!part.done) {
            const value = part.value;
            size += value.byteLength;
            if (size > 262144) {
              await reader.cancel();
              throw new Error("oversize");
            }
            chunks.push(value);
            part = await reader.read();
          }
        } finally {
          reader.releaseLock();
        }
        const bytes = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) {
          bytes.set(chunk, offset);
          offset += chunk.byteLength;
        }
        const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        const data = teamSnapshot.parse(JSON.parse(text));
        if (data.instance !== source.name) throw new Error("wrong instance");
        return { name: source.name, snapshot: data, error: null };
      } catch {
        return { name: source.name, snapshot: null, error: "Unavailable — counts are unknown" };
      }
    }),
  );
}
