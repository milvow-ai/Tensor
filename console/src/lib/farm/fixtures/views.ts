// Derives the five Console views from the fixture world. Each function mirrors the matching view in
// console/sql/views.sql (same columns, same rules) so fixtures and Supabase return identical shapes.

import type {
  AccountDot,
  CapabilityCapacityRow,
  ConnectionStatusRow,
  EffectiveState,
  PoolHealth,
  PoolOverviewRow,
  RunRow,
  SpendMonthRow,
} from "../types";
import type { World, WorldConnection, WorldUnit } from "./world";

export function effectiveState(connection: WorldConnection, now: number): EffectiveState {
  if (connection.status !== "active") return connection.status;
  if (connection.health.circuit === "open") return "circuit_open";
  const cooldown = connection.health.cooldownUntil;
  if (cooldown && new Date(cooldown).getTime() > now) return "cooldown";
  return "active";
}

function unitRemaining(unit: WorldUnit): number | null {
  if (unit.limit === null) return null;
  return Math.max(0, unit.limit - unit.used - unit.reserved);
}

function unitCallsRemaining(unit: WorldUnit): number | null {
  const remaining = unitRemaining(unit);
  if (remaining === null) return null;
  return unit.estimatePerCall <= 0 ? remaining : Math.floor(remaining / unit.estimatePerCall);
}

/** Runs of the fixture list that finished on this account since 00:00 UTC (the SQL view counts every such run). */
function runsToday(world: World, connectionId: string, now: number): number {
  const startOfDay = new Date(now);
  startOfDay.setUTCHours(0, 0, 0, 0);
  return world.runs.filter((run) => run.connection_id === connectionId && new Date(run.started_at) >= startOfDay)
    .length;
}

export function connectionStatusRows(world: World, now: number = Date.now()): ConnectionStatusRow[] {
  const rows: ConnectionStatusRow[] = [];
  for (const conn of world.connections) {
    const provider = world.providers.find((p) => p.id === conn.providerId);
    if (!provider) continue;
    const base = {
      connection_id: conn.id,
      provider_id: conn.providerId,
      provider_name: provider.name,
      provider_kind: provider.kind,
      label: conn.label,
      auth_ref: conn.authRef,
      scope: conn.scope,
      priority: conn.priority,
      strategy: conn.strategy ?? provider.defaultStrategy,
      concurrency: conn.concurrency,
      rate_per_min: conn.ratePerMin,
      status: conn.status,
      effective_state: effectiveState(conn, now),
      plan: conn.plan,
      plan_price_usd: typeof conn.plan.price_usd === "number" ? conn.plan.price_usd : null,
      meta: conn.meta,
      circuit: conn.health.circuit,
      consecutive_failures: conn.health.consecutiveFailures,
      last_error_kind: conn.health.lastErrorKind,
      last_error: conn.health.lastError,
      last_error_at: conn.health.lastErrorAt,
      cooldown_until: conn.health.cooldownUntil,
      success_count: conn.health.successCount,
      failure_count: conn.health.failureCount,
      last_success_at: conn.health.lastSuccessAt,
      latency_ms_p50: conn.health.latencyMsP50,
      sessions_count: conn.sessionsCount,
      calls_today: conn.callsToday + runsToday(world, conn.id, now),
    };
    if (conn.units.length === 0) {
      rows.push({
        ...base,
        unit: null,
        period: null,
        reset_anchor: null,
        next_reset_at: null,
        charged_on: null,
        unit_cost_usd: null,
        estimate_per_call: null,
        used: null,
        reserved: null,
        limit_value: null,
        remaining: null,
        calls_remaining: null,
      });
      continue;
    }
    for (const unit of conn.units) {
      rows.push({
        ...base,
        unit: unit.unit,
        period: unit.period,
        reset_anchor: unit.resetAnchor,
        next_reset_at: unit.nextResetAt,
        charged_on: unit.chargedOn,
        unit_cost_usd: unit.unitCostUsd,
        estimate_per_call: unit.estimatePerCall,
        used: unit.used,
        reserved: unit.reserved,
        limit_value: unit.limit,
        remaining: unitRemaining(unit),
        calls_remaining: unitCallsRemaining(unit),
      });
    }
  }
  return rows;
}

export function poolOverviewRows(world: World, now: number = Date.now()): PoolOverviewRow[] {
  return world.providers.map((provider) => {
    const accounts = world.connections
      .filter((c) => c.providerId === provider.id)
      .sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id))
      .map((conn) => {
        const calls = conn.units.map(unitCallsRemaining).filter((v): v is number => v !== null);
        return {
          conn,
          state: effectiveState(conn, now),
          callsRemaining: calls.length > 0 ? Math.min(...calls) : null,
          noLimit: calls.length === 0,
        };
      });
    const count = (predicate: (a: (typeof accounts)[number]) => boolean) => accounts.filter(predicate).length;
    const usable = accounts.filter((a) => a.state === "active");
    const unusable = count((a) => ["needs_login", "exhausted", "circuit_open", "cooldown"].includes(a.state));
    let health: PoolHealth = "healthy";
    if (!provider.enabled) health = "disabled";
    else if (usable.length === 0) health = "down";
    else if (unusable > 0) health = "degraded";
    const dots: AccountDot[] = accounts.map((a) => ({ id: a.conn.id, label: a.conn.label, state: a.state }));
    return {
      provider_id: provider.id,
      provider_name: provider.name,
      kind: provider.kind,
      executor: provider.executor,
      default_strategy: provider.defaultStrategy,
      enabled: provider.enabled,
      accounts_total: accounts.length,
      accounts_active: count((a) => a.conn.status === "active"),
      accounts_paused: count((a) => a.conn.status === "paused"),
      accounts_needs_login: count((a) => a.conn.status === "needs_login"),
      accounts_exhausted: count((a) => a.conn.status === "exhausted"),
      accounts_open_circuit: count((a) => a.state === "circuit_open"),
      accounts_cooldown: count((a) => a.state === "cooldown"),
      accounts_usable: usable.length,
      account_dots: dots,
      health,
      remaining_calls: usable.reduce((sum, a) => sum + (a.callsRemaining ?? 0), 0),
      unlimited: usable.some((a) => a.noLimit),
      monthly_plan_usd:
        Math.round(
          accounts
            .filter((a) => a.conn.status !== "disabled")
            .reduce((sum, a) => sum + (a.conn.plan.price_usd ?? 0), 0) * 100,
        ) / 100,
    };
  });
}

export function spendMonthRows(world: World, now: number = Date.now()): SpendMonthRow[] {
  const date = new Date(now);
  const monthStart = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
  const elapsedDays = Math.max((now - monthStart) / 86_400_000, 1);
  const daysInMonth = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  const forecast = (spend: number) => Math.round((spend / elapsedDays) * daysInMonth * 100) / 100;
  const round = (value: number) => Math.round(value * 100) / 100;

  const rows: SpendMonthRow[] = world.providers.map((provider) => {
    const entry = world.spend[provider.id] ?? { usageUsd: 0, billingUsd: 0 };
    const spend = entry.usageUsd + entry.billingUsd;
    return {
      provider_id: provider.id,
      provider_name: provider.name,
      kind: provider.kind,
      usage_usd: round(entry.usageUsd),
      billing_usd: round(entry.billingUsd),
      spend_usd: round(spend),
      budget_usd: world.providerBudgets[provider.id] ?? null,
      forecast_usd: forecast(spend),
      elapsed_days: elapsedDays,
      days_in_month: daysInMonth,
    };
  });
  const usage = rows.reduce((sum, row) => sum + row.usage_usd, 0);
  const billing = rows.reduce((sum, row) => sum + row.billing_usd, 0);
  rows.push({
    provider_id: "total",
    provider_name: "Total",
    kind: "all",
    usage_usd: round(usage),
    billing_usd: round(billing),
    spend_usd: round(usage + billing),
    budget_usd: world.globalBudgetUsd,
    forecast_usd: forecast(usage + billing),
    elapsed_days: elapsedDays,
    days_in_month: daysInMonth,
  });
  return rows;
}

export function capabilityCapacityRows(world: World, now: number = Date.now()): CapabilityCapacityRow[] {
  const pools = poolOverviewRows(world, now);
  const rows: CapabilityCapacityRow[] = [];
  for (const capability of world.capabilities) {
    const routes = world.routes
      .filter((route) => route.capability === capability.name)
      .sort((a, b) => a.position - b.position);
    for (const route of routes) {
      const pool = pools.find((p) => p.provider_id === route.providerId);
      if (!pool) continue;
      rows.push({
        capability: capability.name,
        kind: capability.kind,
        description: capability.description,
        default_strategy: capability.defaultStrategy,
        route_position: route.position,
        route_enabled: route.enabled,
        provider_id: pool.provider_id,
        provider_name: pool.provider_name,
        health: pool.health,
        accounts_usable: pool.accounts_usable,
        accounts_total: pool.accounts_total,
        account_dots: pool.account_dots,
        remaining_calls: pool.remaining_calls,
        unlimited: pool.unlimited,
      });
    }
  }
  return rows;
}

export function recentRunRows(world: World): RunRow[] {
  return [...world.runs].sort((a, b) => b.started_at.localeCompare(a.started_at));
}
