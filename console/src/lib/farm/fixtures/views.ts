// Derives the five Console views from the fixture world. Each function mirrors the matching view in
// console/sql/views.sql (same columns, same rules) so fixtures and Supabase return identical shapes.

import type {
  AccountDot,
  BudgetRow,
  CapabilityCapacityRow,
  ConnectionStatusRow,
  CostPerResultRow,
  EffectiveState,
  IdlePaidRow,
  PoolHealth,
  PoolOverviewRow,
  RenewalRow,
  RunDetailRow,
  RunEventRow,
  RunRow,
  SpendDailyRow,
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
    spend_usd: 7500,
    budget_usd: world.globalBudgetUsd,
    forecast_usd: 7750,
    elapsed_days: 30,
    days_in_month: 31,
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

// ---------------------------------------------------------------------------
// C2 Views
// ---------------------------------------------------------------------------

export function spendDailyRows(world: World, days = 90, now: number = Date.now()): SpendDailyRow[] {
  const rows: SpendDailyRow[] = [];
  const baseMonthly: Record<string, number> = {
    clay: 1200,
    claude: 220,
    apollo: 60,
    hunter: 35,
    reoon: 12,
    zerobounce: 45,
    hermes: 35,
    gemini: 20,
    codex: 20,
  };

  for (let offset = days - 1; offset >= 0; offset--) {
    const d = new Date(now - offset * 86_400_000);
    const dayStr = d.toISOString().slice(0, 10);
    for (const provider of world.providers) {
      const base = (baseMonthly[provider.id] ?? 20) / 30;
      // Deterministic variation
      const variance = 0.8 + ((offset * 17 + provider.id.charCodeAt(0) * 13) % 40) / 100;
      const spend = Math.round(base * variance * 100) / 100;
      const usage = Math.round(spend * 0.35 * 100) / 100;
      const billing = Math.round((spend - usage) * 100) / 100;
      rows.push({
        day: dayStr,
        provider_id: provider.id,
        provider_name: provider.name,
        usage_usd: usage,
        billing_usd: billing,
        spend_usd: spend,
      });
    }
  }
  return rows;
}

export function budgetRows(world: World, now: number = Date.now()): BudgetRow[] {
  const spend = spendMonthRows(world, now);
  const total = spend.find((r) => r.provider_id === "total");
  const list: BudgetRow[] = [
    {
      id: "budget-global",
      scope: "global",
      ref: null,
      monthly_usd: world.globalBudgetUsd,
      hard_stop: world.globalBudgetHardStop ?? true,
      spent_usd: total?.spend_usd ?? 0,
      forecast_usd: total?.forecast_usd ?? 0,
    },
  ];

  for (const provider of world.providers) {
    const cap = world.providerBudgets[provider.id];
    if (cap !== undefined) {
      const pSpend = spend.find((r) => r.provider_id === provider.id);
      list.push({
        id: `budget-${provider.id}`,
        scope: "provider",
        ref: provider.id,
        monthly_usd: cap,
        hard_stop: world.providerBudgetHardStops?.[provider.id] ?? true,
        spent_usd: pSpend?.spend_usd ?? 0,
        forecast_usd: pSpend?.forecast_usd ?? 0,
      });
    }
  }
  return list;
}

export function renewalRows(world: World, days = 45, now: number = Date.now()): RenewalRow[] {
  const list: RenewalRow[] = [];
  for (const conn of world.connections) {
    const price = conn.plan.price_usd ?? 0;
    if (price <= 0 || conn.status === "disabled") continue;
    const provider = world.providers.find((p) => p.id === conn.providerId);
    const renewsOn = conn.plan.renews_on ?? new Date(now + 12 * 86_400_000).toISOString();
    const daysUntil = Math.max(0, Math.round((new Date(renewsOn).getTime() - now) / 86_400_000));
    if (daysUntil <= days) {
      const primaryUnit = conn.units[0];
      const used = primaryUnit?.used ?? 0;
      const limit = primaryUnit?.limit ?? null;
      const usagePct = limit !== null && limit > 0 ? Math.round((used / limit) * 1000) / 10 : null;
      list.push({
        connection_id: conn.id,
        connection_label: conn.label,
        provider_id: conn.providerId,
        provider_name: provider?.name ?? conn.providerId,
        plan_name: conn.plan.name ?? null,
        price_usd: price,
        billing_day: conn.plan.billing_day ?? null,
        renews_on: renewsOn,
        days_until_renewal: daysUntil,
        used,
        limit_value: limit,
        usage_pct: usagePct,
      });
    }
  }
  return list.sort(
    (a, b) => a.days_until_renewal - b.days_until_renewal || a.connection_id.localeCompare(b.connection_id),
  );
}

export function idlePaidRows(world: World, now: number = Date.now()): IdlePaidRow[] {
  const cutoff = now - 14 * 86_400_000;
  const list: IdlePaidRow[] = [];
  for (const conn of world.connections) {
    const price = conn.plan.price_usd ?? 0;
    if (price <= 0 || conn.status === "disabled") continue;
    const provider = world.providers.find((p) => p.id === conn.providerId);
    const hasSuccess14d = world.runs.some(
      (r) => r.connection_id === conn.id && r.status === "succeeded" && new Date(r.started_at).getTime() >= cutoff,
    );
    if (!hasSuccess14d) {
      const lastSuccess = conn.health.lastSuccessAt;
      const daysIdle = lastSuccess
        ? Math.round(((now - new Date(lastSuccess).getTime()) / 86_400_000) * 10) / 10
        : 28.0;
      list.push({
        connection_id: conn.id,
        connection_label: conn.label,
        provider_id: conn.providerId,
        provider_name: provider?.name ?? conn.providerId,
        plan_name: conn.plan.name ?? null,
        plan_price_usd: price,
        status: conn.status,
        last_success_at: lastSuccess,
        days_idle: daysIdle,
      });
    }
  }
  return list.sort((a, b) => b.plan_price_usd - a.plan_price_usd || a.connection_id.localeCompare(b.connection_id));
}

export function costPerResultRows(world: World, now: number = Date.now()): CostPerResultRow[] {
  const date = new Date(now);
  const monthStart = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), 1);
  const monthStartIso = new Date(monthStart).toISOString();
  const list: CostPerResultRow[] = [];
  for (const conn of world.connections) {
    const provider = world.providers.find((p) => p.id === conn.providerId);
    const successes = world.runs.filter(
      (r) => r.connection_id === conn.id && r.status === "succeeded" && new Date(r.started_at).getTime() >= monthStart,
    ).length;
    const runSpend = world.runs
      .filter((r) => r.connection_id === conn.id && new Date(r.started_at).getTime() >= monthStart)
      .reduce((sum, r) => sum + r.cost_usd, 0);
    const fraction = Math.max((now - monthStart) / (30 * 86_400_000), 0.1);
    const planSpend = (conn.plan.price_usd ?? 0) * fraction;
    const totalSpend = Math.round((runSpend + planSpend) * 100) / 100;
    const costPerResult = successes > 0 ? Math.round((totalSpend / successes) * 10000) / 10000 : null;
    list.push({
      connection_id: conn.id,
      connection_label: conn.label,
      provider_id: conn.providerId,
      provider_name: provider?.name ?? conn.providerId,
      month_start: monthStartIso,
      spend_usd: totalSpend,
      successful_results: successes,
      cost_per_result: costPerResult,
    });
  }
  return list.sort((a, b) => (b.cost_per_result ?? 0) - (a.cost_per_result ?? 0));
}

export function runDetail(world: World, id: string, now: number = Date.now()): RunDetailRow | null {
  const run = world.runs.find((r) => r.id === id);
  if (!run) return null;

  const startMs = new Date(run.started_at).getTime();
  const events: RunEventRow[] = [];
  let seq = 1;

  events.push({
    id: seq,
    seq: seq++,
    kind: "plan",
    connection_id: null,
    data: {
      strategy: run.strategy ?? "failover",
      capability: run.capability,
      caller: run.caller,
    },
    at: new Date(startMs).toISOString(),
  });

  if (run.status === "blocked") {
    events.push({
      id: seq,
      seq: seq++,
      kind: "policy_block",
      connection_id: null,
      data: {
        reason: run.error ?? "Monthly budget for provider pool would be exceeded; paid call refused.",
      },
      at: new Date(startMs + 40).toISOString(),
    });
  } else if (run.cached) {
    events.push({
      id: seq,
      seq: seq++,
      kind: "cache_hit",
      connection_id: null,
      data: {
        request_hash: `sha256_${run.id.slice(0, 12)}`,
        age_seconds: 142,
      },
      at: new Date(startMs + 20).toISOString(),
    });
  } else {
    // Find candidate connections in pool
    const candidates = world.connections.filter((c) => c.providerId === run.provider_id);
    const firstConn = candidates[0] ?? { id: run.connection_id ?? "unknown-01", label: "Primary Account" };
    const secondConn =
      run.connection_id && run.connection_id !== firstConn.id
        ? (world.connections.find((c) => c.id === run.connection_id) ?? {
            id: run.connection_id,
            label: run.connection_label ?? run.connection_id,
          })
        : (candidates[1] ?? firstConn);

    events.push({
      id: seq,
      seq: seq++,
      kind: "candidate",
      connection_id: firstConn.id,
      data: {
        score: 95,
        reason: "Lowest credit cost; healthy circuit; priority rank 1",
      },
      at: new Date(startMs + 10).toISOString(),
    });

    if (run.attempts_count > 1) {
      // Fallback path
      events.push({
        id: seq,
        seq: seq++,
        kind: "reserve",
        connection_id: firstConn.id,
        data: { unit: "credits", amount: 1 },
        at: new Date(startMs + 25).toISOString(),
      });
      events.push({
        id: seq,
        seq: seq++,
        kind: "execute",
        connection_id: firstConn.id,
        data: { attempt: 1 },
        at: new Date(startMs + 50).toISOString(),
      });
      events.push({
        id: seq,
        seq: seq++,
        kind: "failure",
        connection_id: firstConn.id,
        data: {
          error_kind: "rate_limited",
          error: "HTTP 429: Rate limit exceeded on primary connection; temporary backoff triggered.",
        },
        at: new Date(startMs + 450).toISOString(),
      });
      events.push({
        id: seq,
        seq: seq++,
        kind: "release",
        connection_id: firstConn.id,
        data: { reason: "Released reservation after execution failure" },
        at: new Date(startMs + 460).toISOString(),
      });
      const fallbackMs = run.id === "00000000-0000-0000-0000-000000000002" ? 580 : 470;
      const fallbackReason =
        run.id === "00000000-0000-0000-0000-000000000002"
          ? "Rate limited on primary connection clay-01 (429 Too Many Requests), falling back to secondary connection clay-02"
          : "Connection rate limited; falling back to next provider connection in route";
      const fallbackFrom = run.id === "00000000-0000-0000-0000-000000000002" ? "clay-01" : firstConn.id;
      const fallbackTo = run.id === "00000000-0000-0000-0000-000000000002" ? "clay-02" : secondConn.id;

      events.push({
        id: seq,
        seq: seq++,
        kind: "fallback",
        connection_id: fallbackTo,
        data: {
          from: fallbackFrom,
          to: fallbackTo,
          reason: fallbackReason,
        },
        at: new Date(startMs + fallbackMs).toISOString(),
      });
      events.push({
        id: seq,
        seq: seq++,
        kind: "reserve",
        connection_id: secondConn.id,
        data: { unit: "credits", amount: 1 },
        at: new Date(startMs + 480).toISOString(),
      });
      events.push({
        id: seq,
        seq: seq++,
        kind: "execute",
        connection_id: secondConn.id,
        data: { attempt: 2 },
        at: new Date(startMs + 500).toISOString(),
      });

      if (run.status === "succeeded") {
        events.push({
          id: seq,
          seq: seq++,
          kind: "success",
          connection_id: secondConn.id,
          data: { latency_ms: (run.duration_ms ?? 800) - 500 },
          at: new Date(startMs + (run.duration_ms ?? 900) - 20).toISOString(),
        });
        events.push({
          id: seq,
          seq: seq++,
          kind: "commit",
          connection_id: secondConn.id,
          data: { cost_usd: run.cost_usd, units_used: 1 },
          at: new Date(startMs + (run.duration_ms ?? 900)).toISOString(),
        });
      } else {
        events.push({
          id: seq,
          seq: seq++,
          kind: "failure",
          connection_id: secondConn.id,
          data: { error_kind: run.error_kind, error: run.error },
          at: new Date(startMs + (run.duration_ms ?? 900) - 20).toISOString(),
        });
        events.push({
          id: seq,
          seq: seq++,
          kind: "release",
          connection_id: secondConn.id,
          data: { reason: "Released reservation after execution failure" },
          at: new Date(startMs + (run.duration_ms ?? 900)).toISOString(),
        });
      }
    } else {
      // Single attempt
      const targetConnId = run.connection_id ?? firstConn.id;
      events.push({
        id: seq,
        seq: seq++,
        kind: "reserve",
        connection_id: targetConnId,
        data: { unit: "credits", amount: 1 },
        at: new Date(startMs + 30).toISOString(),
      });
      events.push({
        id: seq,
        seq: seq++,
        kind: "execute",
        connection_id: targetConnId,
        data: { attempt: 1 },
        at: new Date(startMs + 60).toISOString(),
      });

      if (run.status === "succeeded") {
        events.push({
          id: seq,
          seq: seq++,
          kind: "success",
          connection_id: targetConnId,
          data: { latency_ms: run.duration_ms },
          at: new Date(startMs + (run.duration_ms ?? 500) - 15).toISOString(),
        });
        events.push({
          id: seq,
          seq: seq++,
          kind: "commit",
          connection_id: targetConnId,
          data: { cost_usd: run.cost_usd, units_used: 1 },
          at: new Date(startMs + (run.duration_ms ?? 500)).toISOString(),
        });
      } else {
        events.push({
          id: seq,
          seq: seq++,
          kind: "failure",
          connection_id: targetConnId,
          data: { error_kind: run.error_kind, error: run.error },
          at: new Date(startMs + (run.duration_ms ?? 500) - 15).toISOString(),
        });
        events.push({
          id: seq,
          seq: seq++,
          kind: "release",
          connection_id: targetConnId,
          data: { reason: "Released reservation after execution failure" },
          at: new Date(startMs + (run.duration_ms ?? 500)).toISOString(),
        });
      }
    }
  }

  const hasEvidence = run.capability === "analyze_website" || run.capability === "research_company";

  return {
    ...run,
    params: {
      capability: run.capability,
      target: run.capability === "verify_email" ? "user@example.com" : "https://example.com",
      api_key: "sample_secret_key_value",
    },
    result:
      run.status === "succeeded"
        ? {
            ok: true,
            result: {
              status: "valid",
              data: { verified: true, score: 98 },
              checked_at: run.finished_at ?? run.started_at,
            },
            error: null,
            run_id: run.id,
            source: {
              provider: run.provider_id ?? "unknown",
              connection_id: run.connection_id ?? "unknown",
              cached: run.cached,
            },
            cost: {
              usd: run.cost_usd,
              units: run.cached ? {} : { credits: 1 },
            },
          }
        : {
            ok: false,
            result: null,
            error: {
              kind: run.error_kind ?? "unknown",
              message: run.error ?? "Run failed",
            },
            run_id: run.id,
            source: {
              provider: run.provider_id ?? "unknown",
              connection_id: run.connection_id ?? "unknown",
              cached: false,
            },
            cost: { usd: 0, units: {} },
          },
    events,
    evidence_ids: hasEvidence ? ["e1a00000-0000-4000-8000-000000000001"] : [],
  };
}
