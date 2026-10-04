import type { CapabilityCapacityRow, ProviderKind } from "@/lib/farm/types";

export interface CapacityPool {
  providerId: string;
  providerName: string;
  position: number;
  remaining: number;
  unlimited: boolean;
  usable: number;
  total: number;
}

export interface CapacityRow {
  capability: string;
  kind: ProviderKind;
  description: string;
  strategy: string | null;
  total: number;
  unlimited: boolean;
  pools: CapacityPool[];
}

/** Groups `v_capability_capacity` rows (one per capability and pool, route order) into one row per capability. */
export function groupCapacity(rows: CapabilityCapacityRow[]): CapacityRow[] {
  const byCapability = new Map<string, CapacityRow>();
  for (const row of rows) {
    if (!row.route_enabled) continue;
    let entry = byCapability.get(row.capability);
    if (!entry) {
      entry = {
        capability: row.capability,
        kind: row.kind,
        description: row.description,
        strategy: row.default_strategy,
        total: 0,
        unlimited: false,
        pools: [],
      };
      byCapability.set(row.capability, entry);
    }
    entry.pools.push({
      providerId: row.provider_id,
      providerName: row.provider_name,
      position: row.route_position,
      remaining: row.remaining_calls,
      unlimited: row.unlimited,
      usable: row.accounts_usable,
      total: row.accounts_total,
    });
    entry.total += row.remaining_calls;
    entry.unlimited ||= row.unlimited;
  }
  for (const entry of byCapability.values()) entry.pools.sort((a, b) => a.position - b.position);
  return [...byCapability.values()];
}
