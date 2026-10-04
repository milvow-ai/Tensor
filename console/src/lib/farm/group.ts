import type { Connection, ConnectionStatusRow } from "./types";

/** PostgREST may hand numerics back as strings for very large values; normalise everything numeric. */
export function toNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : fallback;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

export function toNullableNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const parsed = toNumber(value, Number.NaN);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * Groups `v_connection_status` rows (one per connection and unit) into one `Connection` per account, keeping the
 * order of first appearance so callers control sorting in the query.
 */
export function groupConnectionRows(rows: ConnectionStatusRow[]): Connection[] {
  const byId = new Map<string, Connection>();
  for (const row of rows) {
    let connection = byId.get(row.connection_id);
    if (!connection) {
      connection = {
        id: row.connection_id,
        providerId: row.provider_id,
        providerName: row.provider_name,
        providerKind: row.provider_kind,
        label: row.label,
        authRef: row.auth_ref,
        scope: row.scope,
        priority: toNumber(row.priority, 100),
        strategy: row.strategy,
        concurrency: toNumber(row.concurrency, 1),
        ratePerMin: toNullableNumber(row.rate_per_min),
        status: row.status,
        effectiveState: row.effective_state,
        plan: row.plan,
        meta: row.meta,
        health: {
          circuit: row.circuit,
          consecutiveFailures: toNumber(row.consecutive_failures),
          lastErrorKind: row.last_error_kind,
          lastError: row.last_error,
          lastErrorAt: row.last_error_at,
          cooldownUntil: row.cooldown_until,
          successCount: toNumber(row.success_count),
          failureCount: toNumber(row.failure_count),
          lastSuccessAt: row.last_success_at,
          latencyMsP50: toNullableNumber(row.latency_ms_p50),
        },
        sessionsCount: toNumber(row.sessions_count),
        callsToday: toNumber(row.calls_today),
        units: [],
      };
      byId.set(row.connection_id, connection);
    }
    if (row.unit !== null && row.period !== null) {
      connection.units.push({
        unit: row.unit,
        period: row.period,
        used: toNumber(row.used),
        reserved: toNumber(row.reserved),
        limit: toNullableNumber(row.limit_value),
        remaining: toNullableNumber(row.remaining),
        callsRemaining: toNullableNumber(row.calls_remaining),
        nextResetAt: row.next_reset_at,
        resetAnchor: toNullableNumber(row.reset_anchor),
        chargedOn: row.charged_on,
        unitCostUsd: toNumber(row.unit_cost_usd),
        estimatePerCall: toNumber(row.estimate_per_call, 1),
      });
    }
  }
  return [...byId.values()];
}
