// Supabase data source: reads the `v_*` views and tables with the signed-in owner's session (RLS applies) and
// writes commands into `farm_commands`. The Console never holds a provider secret and never calls the Farm.

import type { PostgrestError } from "@supabase/supabase-js";

import { createClient } from "@/lib/supabase/server";

import { groupConnectionRows, toNullableNumber, toNumber } from "./group";
import type {
  AlertRow,
  AuditEventRow,
  AuditQuery,
  BillingOverview,
  BudgetRow,
  CapabilityCapacityRow,
  CommandKind,
  Connection,
  ConnectionQuery,
  ConnectionStatusRow,
  CostPerResultRow,
  EvidenceQuery,
  EvidenceRow,
  FactQuery,
  FactRow,
  FarmCommand,
  FarmData,
  FarmOverview,
  IdlePaidRow,
  JsonObject,
  Page,
  PoolDetail,
  PoolOverviewRow,
  ProviderKind,
  RenewalRow,
  RouteRow,
  RunDetailRow,
  RunQuery,
  RunRow,
  SpendDailyRow,
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

function normalizeSpendDaily(row: SpendDailyRow): SpendDailyRow {
  return {
    ...row,
    usage_usd: toNumber(row.usage_usd),
    billing_usd: toNumber(row.billing_usd),
    spend_usd: toNumber(row.spend_usd),
  };
}

function normalizeCostPerResult(row: CostPerResultRow): CostPerResultRow {
  return {
    ...row,
    spend_usd: toNumber(row.spend_usd),
    successful_results: toNumber(row.successful_results),
    cost_per_result: toNullableNumber(row.cost_per_result),
  };
}

function normalizeRenewal(row: RenewalRow): RenewalRow {
  return {
    ...row,
    price_usd: toNumber(row.price_usd),
    billing_day: toNullableNumber(row.billing_day),
    days_until_renewal: toNumber(row.days_until_renewal),
    used: toNullableNumber(row.used),
    limit_value: toNullableNumber(row.limit_value),
    usage_pct: toNullableNumber(row.usage_pct),
  };
}

function normalizeIdlePaid(row: IdlePaidRow): IdlePaidRow {
  return {
    ...row,
    plan_price_usd: toNumber(row.plan_price_usd),
    days_idle: toNumber(row.days_idle),
  };
}

function normalizeBudget(row: BudgetRow): BudgetRow {
  return {
    ...row,
    monthly_usd: toNumber(row.monthly_usd),
    spent_usd: toNullableNumber(row.spent_usd) ?? undefined,
    forecast_usd: toNullableNumber(row.forecast_usd) ?? undefined,
  };
}

function normalizeRunDetail(row: RunDetailRow): RunDetailRow {
  return {
    ...normalizeRun(row),
    params: row.params ?? null,
    result: row.result ?? null,
    events: row.events,
    evidence_ids: row.evidence_ids,
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

  async getBillingOverview(dailyDays = 90): Promise<BillingOverview> {
    const supabase = await createClient();
    const cutoff = new Date(Date.now() - dailyDays * 86_400_000).toISOString().slice(0, 10);
    const [spendMonth, spendDaily, budgets, renewals, costPerResult, idlePaid, paidCount] = await Promise.all([
      supabase.from("v_spend_month").select("*"),
      supabase.from("v_spend_daily").select("*").gte("day", cutoff).order("day", { ascending: false }),
      supabase.from("budgets").select("*"),
      supabase.from("v_renewals").select("*").order("renews_on", { ascending: true }),
      supabase.from("v_cost_per_result").select("*"),
      supabase.from("v_idle_paid").select("*"),
      supabase
        .from("connections")
        .select("id", { count: "exact", head: true })
        .neq("status", "disabled")
        .not("plan->price_usd", "is", null),
    ]);

    const spendMonthRows = rows<SpendMonthRow>(spendMonth, "v_spend_month").map(normalizeSpend);
    const spendMonthMap = new Map(spendMonthRows.map((r) => [r.provider_id, r]));
    const totalSpend = spendMonthMap.get("total");

    const rawBudgets = rows<BudgetRow>(budgets, "budgets").map(normalizeBudget);
    const enrichedBudgets: BudgetRow[] = rawBudgets.map((b) => {
      let match = null;
      if (b.scope === "global") {
        match = totalSpend;
      } else if (b.ref) {
        match = spendMonthMap.get(b.ref);
      }
      return {
        ...b,
        spent_usd: match?.spend_usd ?? 0,
        forecast_usd: match?.forecast_usd ?? 0,
      };
    });

    const idlePaidRows = rows<IdlePaidRow>(idlePaid, "v_idle_paid").map(normalizeIdlePaid);

    return {
      spendMonth: spendMonthRows,
      spendDaily: rows<SpendDailyRow>(spendDaily, "v_spend_daily").map(normalizeSpendDaily),
      budgets: enrichedBudgets,
      renewals: rows<RenewalRow>(renewals, "v_renewals").map(normalizeRenewal),
      costPerResult: rows<CostPerResultRow>(costPerResult, "v_cost_per_result").map(normalizeCostPerResult),
      idlePaid: idlePaidRows,
      paidAccountsCount: paidCount.count ?? 0,
      idlePaidCount: idlePaidRows.length,
    };
  }

  async listSpendDaily(days = 90): Promise<SpendDailyRow[]> {
    const supabase = await createClient();
    const cutoff = new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
    const result = await supabase
      .from("v_spend_daily")
      .select("*")
      .gte("day", cutoff)
      .order("day", { ascending: false });
    return rows<SpendDailyRow>(result, "v_spend_daily").map(normalizeSpendDaily);
  }

  async listBudgets(): Promise<BudgetRow[]> {
    const supabase = await createClient();
    const [budgets, spendMonth] = await Promise.all([
      supabase.from("budgets").select("*"),
      supabase.from("v_spend_month").select("*"),
    ]);
    const spendMonthRows = rows<SpendMonthRow>(spendMonth, "v_spend_month").map(normalizeSpend);
    const spendMonthMap = new Map(spendMonthRows.map((r) => [r.provider_id, r]));
    const totalSpend = spendMonthMap.get("total");
    return rows<BudgetRow>(budgets, "budgets").map((b) => {
      const normalized = normalizeBudget(b);
      let match = null;
      if (normalized.scope === "global") {
        match = totalSpend;
      } else if (normalized.ref) {
        match = spendMonthMap.get(normalized.ref);
      }
      return {
        ...normalized,
        spent_usd: match?.spend_usd ?? 0,
        forecast_usd: match?.forecast_usd ?? 0,
      };
    });
  }

  async listRenewals(_days = 45): Promise<RenewalRow[]> {
    const supabase = await createClient();
    const result = await supabase.from("v_renewals").select("*").order("renews_on", { ascending: true });
    return rows<RenewalRow>(result, "v_renewals").map(normalizeRenewal);
  }

  async listCostPerResult(): Promise<CostPerResultRow[]> {
    const supabase = await createClient();
    const result = await supabase.from("v_cost_per_result").select("*");
    return rows<CostPerResultRow>(result, "v_cost_per_result").map(normalizeCostPerResult);
  }

  async listIdlePaid(): Promise<IdlePaidRow[]> {
    const supabase = await createClient();
    const result = await supabase.from("v_idle_paid").select("*");
    return rows<IdlePaidRow>(result, "v_idle_paid").map(normalizeIdlePaid);
  }

  async listRuns(query: RunQuery = {}): Promise<Page<RunRow>> {
    const { page = 1, pageSize = 20, capability, status, failuresOnly, caller, from, to } = query;
    const supabase = await createClient();
    let q = supabase.from("v_recent_runs").select("*", { count: "exact" });

    if (capability) q = q.eq("capability", capability);
    if (status) q = q.eq("status", status);
    if (failuresOnly) q = q.in("status", ["failed", "blocked"]);
    if (caller) q = q.eq("caller", caller);
    if (from) q = q.gte("started_at", from);
    if (to) q = q.lte("started_at", to);

    const fromIdx = (page - 1) * pageSize;
    q = q.order("started_at", { ascending: false }).range(fromIdx, fromIdx + pageSize - 1);
    const result = await q;
    return {
      items: rows<RunRow>(result, "v_recent_runs").map(normalizeRun),
      total: result.count ?? 0,
      page,
      pageSize,
    };
  }

  async getRunDetail(id: string): Promise<RunDetailRow | null> {
    const supabase = await createClient();
    const result = await supabase.from("v_run_detail").select("*").eq("id", id).maybeSingle();
    if (result.error) throw new FarmDataError("v_run_detail", result.error);
    return result.data ? normalizeRunDetail(result.data as unknown as RunDetailRow) : null;
  }

  // C3: Routing, Memory & Evidence, Policies & Audits
  async listRoutes(capability?: string): Promise<RouteRow[]> {
    const supabase = await createClient();
    let q = supabase.from("v_routes").select("*").order("position", { ascending: true });
    if (capability) q = q.eq("capability", capability);
    const result = await q;
    return rows<RouteRow>(result, "v_routes");
  }

  async listFacts(query: FactQuery = {}): Promise<Page<FactRow>> {
    const { page = 1, pageSize = 20, search, freshness, entityKind } = query;
    const supabase = await createClient();
    let q = supabase.from("v_facts").select("*", { count: "exact" });
    if (freshness && freshness !== "all") q = q.eq("freshness_state", freshness);
    if (entityKind) q = q.eq("entity_kind", entityKind);
    if (search) {
      q = q.or(`entity_name.ilike.%${search}%,entity_canonical_key.ilike.%${search}%,attribute.ilike.%${search}%`);
    }
    const fromIdx = (page - 1) * pageSize;
    q = q.order("observed_at", { ascending: false }).range(fromIdx, fromIdx + pageSize - 1);
    const result = await q;
    return {
      items: rows<FactRow>(result, "v_facts"),
      total: result.count ?? 0,
      page,
      pageSize,
    };
  }

  async getFact(id: string): Promise<FactRow | null> {
    const supabase = await createClient();
    const result = await supabase.from("v_facts").select("*").eq("id", id).maybeSingle();
    if (result.error) throw new FarmDataError("v_facts", result.error);
    return (result.data as FactRow | null) ?? null;
  }

  async listEvidence(query: EvidenceQuery = {}): Promise<Page<EvidenceRow>> {
    const { page = 1, pageSize = 20, search } = query;
    const supabase = await createClient();
    let q = supabase.from("v_evidence").select("*", { count: "exact" });
    if (search) {
      q = q.or(`sha256.ilike.%${search}%,url.ilike.%${search}%,path.ilike.%${search}%`);
    }
    const fromIdx = (page - 1) * pageSize;
    q = q.order("captured_at", { ascending: false }).range(fromIdx, fromIdx + pageSize - 1);
    const result = await q;
    return {
      items: rows<EvidenceRow>(result, "v_evidence"),
      total: result.count ?? 0,
      page,
      pageSize,
    };
  }

  async getEvidence(id: string): Promise<EvidenceRow | null> {
    const supabase = await createClient();
    const result = await supabase.from("v_evidence").select("*").eq("id", id).maybeSingle();
    if (result.error) throw new FarmDataError("v_evidence", result.error);
    return (result.data as EvidenceRow | null) ?? null;
  }

  async listAuditEvents(query: AuditQuery = {}): Promise<Page<AuditEventRow>> {
    const { page = 1, pageSize = 20, actor, action } = query;
    const supabase = await createClient();
    let q = supabase.from("audit_events").select("*", { count: "exact" });
    if (actor) q = q.ilike("actor", `%${actor}%`);
    if (action) q = q.eq("action", action);
    const fromIdx = (page - 1) * pageSize;
    q = q.order("at", { ascending: false }).range(fromIdx, fromIdx + pageSize - 1);
    const result = await q;
    return {
      items: rows<AuditEventRow>(result, "audit_events"),
      total: result.count ?? 0,
      page,
      pageSize,
    };
  }
}
