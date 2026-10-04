import { resolveDataSource } from "@/lib/farm/mode";

/**
 * A misconfigured data source is fatal: the process exits instead of serving pages that could show invented data
 * (for example fixtures in a production build).
 */
export function assertDataSource() {
  try {
    resolveDataSource();
  } catch (error) {
    console.error(error instanceof Error ? error.message : error);
    process.exit(1);
  }
}
