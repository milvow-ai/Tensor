import { resolveDataSource } from "./mode";
import type { FarmData } from "./types";

/** The data source for this request, chosen by FARM_DATA_SOURCE. Fixtures are imported lazily, never in supabase mode. */
export async function getFarmData(): Promise<FarmData> {
  if (resolveDataSource() === "fixtures") {
    const { FixturesFarmData } = await import("./fixtures/source");
    return new FixturesFarmData();
  }
  const { SupabaseFarmData } = await import("./supabase-source");
  return new SupabaseFarmData();
}

export type Attempt<T> = { ok: true; data: T } | { ok: false; message: string };

/** Runs a data call and turns a failure into a message the page can show, instead of crashing the route. */
export async function attempt<T>(run: (data: FarmData) => Promise<T>): Promise<Attempt<T>> {
  try {
    return { ok: true, data: await run(await getFarmData()) };
  } catch (error) {
    // Redirects and Next control-flow errors must keep propagating.
    if (
      error instanceof Error &&
      "digest" in error &&
      String((error as { digest?: unknown }).digest).startsWith("NEXT_")
    ) {
      throw error;
    }
    return { ok: false, message: error instanceof Error ? error.message : "Unexpected error while loading data." };
  }
}
