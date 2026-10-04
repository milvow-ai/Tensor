import type { DataSourceKind } from "./types";

/**
 * Which data source this server uses: `FARM_DATA_SOURCE` = `supabase` (default) or `fixtures`.
 * Fixtures are allowed only outside production, so a deployed Console can never show invented data. The one
 * exception is the Playwright suite: `next start` always runs with NODE_ENV=production, so the end-to-end run sets
 * `FARM_E2E=1` to test the production build against fixtures. Nothing else sets it.
 */
export function resolveDataSource(): DataSourceKind {
  const raw = (process.env.FARM_DATA_SOURCE ?? "supabase").trim().toLowerCase();
  if (raw !== "supabase" && raw !== "fixtures") {
    throw new Error(`FARM_DATA_SOURCE must be "supabase" or "fixtures" (got "${raw}").`);
  }
  if (raw === "fixtures" && process.env.NODE_ENV === "production" && process.env.FARM_E2E !== "1") {
    throw new Error(
      'FARM_DATA_SOURCE="fixtures" is not allowed when NODE_ENV=production. Fixtures are for local development only.',
    );
  }
  return raw;
}
