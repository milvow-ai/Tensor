// Harness Farm data-layer types.
//
// The row types mirror, column for column, what the SQL views in `console/sql/views.sql` return
// (v_connection_status, v_pool_overview, v_spend_month, v_capability_capacity, v_recent_runs) and the
// `alerts` / `farm_commands` tables of briefs/CONTEXT.md section 3. The fixtures source builds rows of exactly
// these shapes, so the UI cannot tell the two sources apart.

export type ProviderKind = "tool" | "ai";

export type ProviderExecutor = "api" | "mcp" | "llm" | "cli_agent" | "agent" | "browser" | "local" | "human";

/** `connections.status` (what the owner or the Farm decided). */
export type ConnectionStatus = "active" | "paused" | "needs_login" | "exhausted" | "disabled";

/**
 * What the router can actually do with an account right now: `status`, refined by the circuit breaker and the
 * cooldown window. Computed in SQL (`effective_state` column) so every consumer agrees.
 */
export type EffectiveState = ConnectionStatus | "circuit_open" | "cooldown";

export type CircuitState = "closed" | "open" | "half_open";

/** Derived per pool in SQL: healthy, degraded (some account unusable), down (none usable), disabled. */
export type PoolHealth = "healthy" | "degraded" | "down" | "disabled";

export type PeriodUnit = "minute" | "hour" | "day" | "week" | "month" | "rolling_5h" | "total" | "none";

export type ChargedOn = "attempt" | "success" | "found";

export const POOL_STRATEGIES = [
  "failover",
  "most_remaining",
  "round_robin",
  "parallel_split",
  "sticky",
  "fit_check",
] as const;
export type PoolStrategy = (typeof POOL_STRATEGIES)[number];

export type CommandKind =
  | "pause"
  | "resume"
  | "set_priority"
  | "set_strategy"
  | "set_budget"
  | "add_connection"
  | "update_connection"
  | "remove_connection"
  | "set_route"
  | "test_connection"
  | "ack_alert";

export type CommandStatus = "queued" | "running" | "done" | "rejected" | "failed";

export type AlertSeverity = "info" | "warn" | "critical";

export type RunStatus = "running" | "succeeded" | "failed" | "blocked";

export type JsonObject = Record<string, unknown>;

/** `connections.plan` jsonb. */
export interface PlanInfo {
  name?: string;
  price_usd?: number;
  billing_day?: number;
  renews_on?: string;
}

/** `connections.meta` jsonb (AI accounts carry `cli`, `config_dir`, `models`). */
export interface ConnectionMeta {
  cli?: string;
  config_dir?: string;
  models?: string[];
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// v_connection_status: one row per (connection, consumption unit)
// ---------------------------------------------------------------------------
export interface ConnectionStatusRow {
  connection_id: string;
  provider_id: string;
  provider_name: string;
  provider_kind: ProviderKind;
  label: string;
  auth_ref: string | null;
  scope: string[];
  priority: number;
  strategy: string | null;
  concurrency: number;
  rate_per_min: number | null;
  status: ConnectionStatus;
  effective_state: EffectiveState;
  plan: PlanInfo;
  plan_price_usd: number | null;
  meta: ConnectionMeta;
  circuit: CircuitState;
  consecutive_failures: number;
  last_error_kind: string | null;
  last_error: string | null;
  last_error_at: string | null;
  cooldown_until: string | null;
  success_count: number;
  failure_count: number;
  last_success_at: string | null;
  latency_ms_p50: number | null;
  sessions_count: number;
  calls_today: number;
  // unit columns: null when the connection has no consumption unit
  unit: string | null;
  period: PeriodUnit | null;
  reset_anchor: number | null;
  next_reset_at: string | null;
  charged_on: ChargedOn | null;
  unit_cost_usd: number | null;
  estimate_per_call: number | null;
  used: number | null;
  reserved: number | null;
  limit_value: number | null;
  remaining: number | null;
  calls_remaining: number | null;
}

/** One account in a pool, as the dots on pool tiles show it (ordered by priority). */
export interface AccountDot {
  id: string;
  label: string;
  state: EffectiveState;
}

// ---------------------------------------------------------------------------
// v_pool_overview: one row per provider pool
// ---------------------------------------------------------------------------
export interface PoolOverviewRow {
  provider_id: string;
  provider_name: string;
  kind: ProviderKind;
  executor: ProviderExecutor;
  default_strategy: string | null;
  enabled: boolean;
  accounts_total: number;
  accounts_active: number;
  accounts_paused: number;
  accounts_needs_login: number;
  accounts_exhausted: number;
  accounts_open_circuit: number;
  accounts_cooldown: number;
  accounts_usable: number;
  account_dots: AccountDot[];
  health: PoolHealth;
  remaining_calls: number;
  unlimited: boolean;
  monthly_plan_usd: number;
}

// ---------------------------------------------------------------------------
// v_spend_month: one row per provider plus a `total` row
// ---------------------------------------------------------------------------
export interface SpendMonthRow {
  provider_id: string;
  provider_name: string;
  kind: ProviderKind | "all";
  usage_usd: number;
  billing_usd: number;
  spend_usd: number;
  budget_usd: number | null;
  forecast_usd: number;
  elapsed_days: number;
  days_in_month: number;
}

// ---------------------------------------------------------------------------
// v_capability_capacity: one row per (capability, pool) in route order
// ---------------------------------------------------------------------------
export interface CapabilityCapacityRow {
  capability: string;
  kind: ProviderKind;
  description: string;
  default_strategy: string | null;
  route_position: number;
  route_enabled: boolean;
  provider_id: string;
  provider_name: string;
  health: PoolHealth;
  accounts_usable: number;
  accounts_total: number;
  account_dots: AccountDot[];
  remaining_calls: number;
  unlimited: boolean;
}

// ---------------------------------------------------------------------------
// v_recent_runs
// ---------------------------------------------------------------------------
export interface RunRow {
  id: string;
  capability: string;
  request_id: string | null;
  caller: string;
  strategy: string | null;
  status: RunStatus;
  cost_usd: number;
  cached: boolean;
  connection_id: string | null;
  connection_label: string | null;
  provider_id: string | null;
  provider_name: string | null;
  error_kind: string | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  attempts_count: number;
}

// ---------------------------------------------------------------------------
// Tables read directly
// ---------------------------------------------------------------------------
export interface AlertRow {
  id: string;
  kind: string;
  severity: AlertSeverity;
  message: string;
  ref: string | null;
  created_at: string;
  acked_at: string | null;
}

export interface FarmCommand {
  id: string;
  kind: CommandKind;
  payload: JsonObject;
  status: CommandStatus;
  result: JsonObject | null;
  created_by: string | null;
  created_at: string;
  done_at: string | null;
}

// ---------------------------------------------------------------------------
// View models assembled by the data layer
// ---------------------------------------------------------------------------

/** One consumption unit of an account, current period. */
export interface UnitUsage {
  unit: string;
  period: PeriodUnit;
  used: number;
  reserved: number;
  limit: number | null;
  remaining: number | null;
  callsRemaining: number | null;
  nextResetAt: string | null;
  resetAnchor: number | null;
  chargedOn: ChargedOn | null;
  unitCostUsd: number;
  estimatePerCall: number;
}

/** An account (connection) with its units grouped from `v_connection_status` rows. */
export interface Connection {
  id: string;
  providerId: string;
  providerName: string;
  providerKind: ProviderKind;
  label: string;
  authRef: string | null;
  scope: string[];
  priority: number;
  strategy: string | null;
  concurrency: number;
  ratePerMin: number | null;
  status: ConnectionStatus;
  effectiveState: EffectiveState;
  plan: PlanInfo;
  meta: ConnectionMeta;
  health: {
    circuit: CircuitState;
    consecutiveFailures: number;
    lastErrorKind: string | null;
    lastError: string | null;
    lastErrorAt: string | null;
    cooldownUntil: string | null;
    successCount: number;
    failureCount: number;
    lastSuccessAt: string | null;
    latencyMsP50: number | null;
  };
  sessionsCount: number;
  callsToday: number;
  units: UnitUsage[];
}

export type ConnectionSortKey = "priority" | "label" | "status";
export type SortDirection = "asc" | "desc";

export interface ConnectionQuery {
  page?: number;
  pageSize?: number;
  sort?: ConnectionSortKey;
  dir?: SortDirection;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface PoolDetail {
  pool: PoolOverviewRow;
  spend: SpendMonthRow | null;
  routes: CapabilityCapacityRow[];
}

export interface FarmOverview {
  generatedAt: string;
  pools: PoolOverviewRow[];
  spend: SpendMonthRow[];
  capacity: CapabilityCapacityRow[];
  runs: RunRow[];
  alerts: AlertRow[];
  commands: FarmCommand[];
}

export type DataSourceKind = "supabase" | "fixtures";

export interface FarmData {
  readonly source: DataSourceKind;
  getOverview(): Promise<FarmOverview>;
  listPools(kind?: ProviderKind): Promise<PoolOverviewRow[]>;
  getPool(id: string): Promise<PoolDetail | null>;
  listConnections(poolId: string, query?: ConnectionQuery): Promise<Page<Connection>>;
  getConnection(id: string): Promise<Connection | null>;
  listRecentRuns(n?: number): Promise<RunRow[]>;
  getRun(id: string): Promise<RunRow | null>;
  listAlerts(): Promise<AlertRow[]>;
  listCommands(n?: number): Promise<FarmCommand[]>;
  getCommand(id: string): Promise<FarmCommand | null>;
  enqueueCommand(kind: CommandKind, payload: JsonObject): Promise<FarmCommand>;
}
