// Supabase data source: reads the `v_*` views and tables with the signed-in owner's session (RLS applies) and
// writes commands into `farm_commands`. The Console never holds a provider secret and never calls the Farm.

import type { PostgrestError } from "@supabase/supabase-js";

import { createClient } from "@/lib/supabase/server";

import { groupConnectionRows, toNullableNumber, toNumber } from "./group";
import type {
  AlertRow,
  CapabilityCapacityRow,
  CommandKind,
  Connection,
  ConnectionQuery,
  ConnectionStatusRow,
  FarmCommand,
  FarmData,
  FarmOverview,
  JsonObject,
  Page,
  PoolDetail,
  PoolOverviewRow,
  ProviderKind,
  RunRow,
  SpendMonthRow,
} from "./types";

export class FarmDataError extends Error {
  constructor(what: string, cause: PostgrestError) {
    super(describe(what, cause));
    this.name = "FarmDataError";
  }
}

function describe(what: string, error: PostgrestError): string {
  // 42P01 = undefined table or view, PGRST205 = not in PostgREST's schema cache.
  if (error.code === "42P01" || error.code === "PGRST205") {
    return `${what}: the view or table is missing. Apply the Farm database migrations (including 0003, the Console views).`;
  }
  if (error.code === "42501") {
    return `${what}: permission denied. Signed-in user is not allowed to read it (row-level security).`;
  }
  return `${what}: ${error.message}`;
}

function rows<T>(result: { data: unknown; error: PostgrestError | null }, what: string): T[] {
  if (result.error) throw new FarmDataError(what, result.error);
  return (result.data ?? []) as T[];
}

function normalizePool(row: PoolOverviewRow): PoolOverviewRow {
  return {
    ...row,
    accounts_total: toNumber(row.accounts_total),
    accounts_active: toNumber(row.accounts_active),
    accounts_paused: toNumber(row.accounts_paused),
    accounts_needs_login: toNumber(row.accounts_needs_login),
    accounts_exhausted: toNumber(row.accounts_exhausted),
    accounts_open_circuit: toNumber(row.accounts_open_circuit),
    accounts_cooldown: toNumber(row.accounts_cooldown),
    accounts_usable: toNumber(row.accounts_usable),
    remaining_calls: toNumber(row.remaining_calls),
    monthly_plan_usd: toNumber(row.monthly_plan_usd),
  };
}

function normalizeSpend(row: SpendMonthRow): SpendMonthRow {
  return {
    ...row,
    usage_usd: toNumber(row.usage_usd),
    billing_usd: toNumber(row.billing_usd),
    spend_usd: toNumber(row.spend_usd),
    budget_usd: toNullableNumber(row.budget_usd),
    forecast_usd: toNumber(row.forecast_usd),
    elapsed_days: toNumber(row.elapsed_days, 1),
    days_in_month: toNumber(row.days_in_month, 30),
  };
}

function normalizeCapacity(row: CapabilityCapacityRow): CapabilityCapacityRow {
  return {
    ...row,
    route_position: toNumber(row.route_position),
    accounts_usable: toNumber(row.accounts_usable),
    accounts_total: toNumber(row.accounts_total),
    remaining_calls: toNumber(row.remaining_calls),
  };
}

function normalizeRun(row: RunRow): RunRow {
  return {
    ...row,
    cost_usd: toNumber(row.cost_usd),
    duration_ms: toNullableNumber(row.duration_ms),
    attempts_count: toNumber(row.attempts_count),
  };
}

const SORT_COLUMN = { priority: "priority", label: "label", status: "status" } as const;

export class SupabaseFarmData implements FarmData {
  readonly source = "supabase" as const;

  async getOverview(): Promise<FarmOverview> {
    const supabase = await createClient();
    const [pools, spend, capacity, runs, alerts, commands] = await Promise.all([
      supabase.from("v_pool_overview").select("*").order("kind").order("provider_id"),
      supabase.from("v_spend_month").select("*"),
      supabase.from("v_capability_capacity").select("*").order("capability").order("route_position"),
      supabase.from("v_recent_runs").select("*").order("started_at", { ascending: false }).limit(8),
      supabase.from("alerts").select("*").is("acked_at", null).order("created_at", { ascending: false }).limit(50),
      supabase.from("farm_commands").select("*").order("created_at", { ascending: false }).limit(6),
    ]);
    return {
      generatedAt: new Date().toISOString(),
      pools: rows<PoolOverviewRow>(pools, "v_pool_overview").map(normalizePool),
      spend: rows<SpendMonthRow>(spend, "v_spend_month").map(normalizeSpend),
      capacity: rows<CapabilityCapacityRow>(capacity, "v_capability_capacity").map(normalizeCapacity),
      runs: rows<RunRow>(runs, "v_recent_runs").map(normalizeRun),
      alerts: rows<AlertRow>(alerts, "alerts"),
      commands: rows<FarmCommand>(commands, "farm_commands"),
    };
  }

  async listPools(kind?: ProviderKind): Promise<PoolOverviewRow[]> {
    const supabase = await createClient();
    let query = supabase.from("v_pool_overview").select("*").order("provider_id");
    if (kind) query = query.eq("kind", kind);
    return rows<PoolOverviewRow>(await query, "v_pool_overview").map(normalizePool);
  }

  async getPool(id: string): Promise<PoolDetail | null> {
    const supabase = await createClient();
    const [pool, spend, routes] = await Promise.all([
      supabase.from("v_pool_overview").select("*").eq("provider_id", id).maybeSingle(),
      supabase.from("v_spend_month").select("*").eq("provider_id", id).maybeSingle(),
      supabase.from("v_capability_capacity").select("*").eq("provider_id", id).order("capability"),
    ]);
    if (pool.error) throw new FarmDataError("v_pool_overview", pool.error);
    if (!pool.data) return null;
    if (spend.error) throw new FarmDataError("v_spend_month", spend.error);
    return {
      pool: normalizePool(pool.data as unknown as PoolOverviewRow),
      spend: spend.data ? normalizeSpend(spend.data as unknown as SpendMonthRow) : null,
      routes: rows<CapabilityCapacityRow>(routes, "v_capability_capacity").map(normalizeCapacity),
    };
  }

  async listConnections(poolId: string, query: ConnectionQuery = {}): Promise<Page<Connection>> {
    const { page = 1, pageSize = 10, sort = "priority", dir = "asc" } = query;
    const supabase = await createClient();
    const from = (page - 1) * pageSize;

    // 1) page of connection ids (pagination and sorting are per account, not per unit row)
    const ids = await supabase
      .from("connections")
      .select("id", { count: "exact" })
      .eq("provider_id", poolId)
      .order(SORT_COLUMN[sort], { ascending: dir === "asc" })
      .order("id")
      .range(from, from + pageSize - 1);
    if (ids.error) throw new FarmDataError("connections", ids.error);
    const pageIds = (ids.data ?? []).map((row) => (row as { id: string }).id);
    if (pageIds.length === 0) return { items: [], total: ids.count ?? 0, page, pageSize };

    // 2) their status rows from the view, then restore the page order
    const statuses = await supabase.from("v_connection_status").select("*").in("connection_id", pageIds);
    const grouped = groupConnectionRows(rows<ConnectionStatusRow>(statuses, "v_connection_status"));
    const order = new Map(pageIds.map((id, index) => [id, index]));
    grouped.sort((a, b) => (order.get(a.id) ?? 0) - (order.get(b.id) ?? 0));
    return { items: grouped, total: ids.count ?? grouped.length, page, pageSize };
  }

  async getConnection(id: string): Promise<Connection | null> {
    const supabase = await createClient();
    const result = await supabase.from("v_connection_status").select("*").eq("connection_id", id);
    return groupConnectionRows(rows<ConnectionStatusRow>(result, "v_connection_status"))[0] ?? null;
  }

  async listRecentRuns(n = 8): Promise<RunRow[]> {
    const supabase = await createClient();
    const result = await supabase.from("v_recent_runs").select("*").order("started_at", { ascending: false }).limit(n);
    return rows<RunRow>(result, "v_recent_runs").map(normalizeRun);
  }

  async getRun(id: string): Promise<RunRow | null> {
    const supabase = await createClient();
    const result = await supabase.from("v_recent_runs").select("*").eq("id", id).maybeSingle();
    if (result.error) throw new FarmDataError("v_recent_runs", result.error);
    return result.data ? normalizeRun(result.data as unknown as RunRow) : null;
  }

  async listAlerts(): Promise<AlertRow[]> {
    const supabase = await createClient();
    const result = await supabase.from("alerts").select("*").order("created_at", { ascending: false }).limit(200);
    return rows<AlertRow>(result, "alerts");
  }

  async listCommands(n = 20): Promise<FarmCommand[]> {
    const supabase = await createClient();
    const result = await supabase.from("farm_commands").select("*").order("created_at", { ascending: false }).limit(n);
    return rows<FarmCommand>(result, "farm_commands");
  }

  async getCommand(id: string): Promise<FarmCommand | null> {
    const supabase = await createClient();
    const result = await supabase.from("farm_commands").select("*").eq("id", id).maybeSingle();
    if (result.error) throw new FarmDataError("farm_commands", result.error);
    return (result.data as FarmCommand | null) ?? null;
  }

  async enqueueCommand(kind: CommandKind, payload: JsonObject): Promise<FarmCommand> {
    const supabase = await createClient();
    const { data: auth } = await supabase.auth.getUser();
    // RLS lets the owner insert only into farm_commands; status defaults to 'queued'.
    const result = await supabase
      .from("farm_commands")
      .insert({ kind, payload, created_by: auth.user?.email ?? null })
      .select("*")
      .single();
    if (result.error) throw new FarmDataError("farm_commands insert", result.error);
    return result.data as FarmCommand;
  }
}
