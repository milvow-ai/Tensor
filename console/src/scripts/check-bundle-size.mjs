// Fails when any route's first-load JavaScript exceeds the budget (250 kB gzipped by default).
//
// Next 16 no longer prints per-route sizes in `next build`, so this reads the route list that the build itself writes
// (.next/diagnostics/route-bundle-stats.json: the chunks each route loads on first visit) and gzips those chunks,
// which is what the browser downloads. Run it after `next build`: `pnpm bundle`.

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const BUDGET_KB = Number(process.env.FARM_BUNDLE_BUDGET_KB ?? 250);
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const statsFile = join(root, ".next", "diagnostics", "route-bundle-stats.json");

let routes;
try {
  routes = JSON.parse(readFileSync(statsFile, "utf8"));
} catch {
  console.error(`No build stats at ${statsFile}. Run "pnpm exec next build" first.`);
  process.exit(2);
}

const gzipCache = new Map();
function gzippedBytes(chunkPath) {
  const file = join(root, chunkPath.replaceAll("\\", "/"));
  if (!gzipCache.has(file)) gzipCache.set(file, gzipSync(readFileSync(file), { level: 9 }).length);
  return gzipCache.get(file);
}

const rows = routes
  .map((entry) => ({
    route: entry.route,
    kb: entry.firstLoadChunkPaths.reduce((sum, chunk) => sum + gzippedBytes(chunk), 0) / 1024,
  }))
  .sort((a, b) => a.route.localeCompare(b.route));

const width = Math.max(...rows.map((row) => row.route.length));
console.log(`First-load JS per route (gzip), budget ${BUDGET_KB} kB`);
for (const row of rows) {
  const flag = row.kb > BUDGET_KB ? "  OVER BUDGET" : "";
  console.log(`  ${row.route.padEnd(width)}  ${row.kb.toFixed(1).padStart(7)} kB${flag}`);
}

const over = rows.filter((row) => row.kb > BUDGET_KB);
if (over.length > 0) {
  console.error(`\n${over.length} route(s) exceed ${BUDGET_KB} kB: ${over.map((row) => row.route).join(", ")}`);
  process.exit(1);
}
console.log("\nAll routes are within budget.");
