import { getFarmData } from "@/lib/farm/data";

export interface AccountPreview {
  id: string;
  label: string;
  status: string;
  remaining: number | null;
  unit: string | null;
  latencyMs: number | null;
  circuit: string;
}

/** Routes plus a per-pool account preview: shared by `/routing` and the Settings → Routing tab. */
export async function loadRouting() {
  const farmData = await getFarmData();
  const routes = await farmData.listRoutes();

  const poolIds = Array.from(new Set(routes.map((r) => r.provider_id)));
  const accountsByPool: Record<string, AccountPreview[]> = {};

  await Promise.all(
    poolIds.map(async (poolId) => {
      const page = await farmData.listConnections(poolId, { pageSize: 50 });
      accountsByPool[poolId] = page.items.map((conn) => ({
        id: conn.id,
        label: conn.label,
        status: conn.status,
        remaining: conn.units[0]?.remaining ?? null,
        unit: conn.units[0]?.unit ?? null,
        latencyMs: conn.health.latencyMsP50,
        circuit: conn.health.circuit,
      }));
    }),
  );

  return { routes, accountsByPool };
}
