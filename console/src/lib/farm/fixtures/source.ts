// Fixtures data source: the fixture world behind the same FarmData interface as Supabase.
// Commands go into an in-memory queue; after ~0.5 s a command is "running" and after 1.5 s "done" (or "rejected"
// with a reason), and only then does the world change, the way the real Farm consumer behaves.

import { type ConnectionSpecPayload, commandPayloadSchemas, isSupportedCommand } from "../commands";
import { groupConnectionRows } from "../group";
import type {
  AlertRow,
  AuditEventRow,
  AuditQuery,
  BillingOverview,
  BudgetRow,
  CommandKind,
  Connection,
  ConnectionQuery,
  ConnectionSortKey,
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
} from "../types";
import {
  auditEventRows,
  budgetRows,
  capabilityCapacityRows,
  connectionStatusRows,
  costPerResultRows,
  evidenceRows,
  factsRows,
  idlePaidRows,
  poolOverviewRows,
  recentRunRows,
  renewalRows,
  routesRows,
  runDetail,
  spendDailyRows,
  spendMonthRows,
} from "./views";
import {
  buildEmptyWorld,
  buildWorld,
  nextMonthlyReset,
  type World,
  type WorldConnection,
  type WorldUnit,
} from "./world";

const QUEUE_RUNNING_MS = 500;
const QUEUE_DONE_MS = 1500;

const store = globalThis as unknown as { __farmFixtureWorld?: World };

function world(): World {
  if (!store.__farmFixtureWorld) {
    const isBlank = process.env.FARM_FIXTURES === "empty";
    store.__farmFixtureWorld = isBlank ? buildEmptyWorld() : buildWorld();
  }
  return store.__farmFixtureWorld;
}

const SEVERITY_RANK = { critical: 0, warn: 1, info: 2 } as const;

class Rejection extends Error {}

function buildUnit(unit: string, spec: ConnectionSpecPayload["units"][string], now: number): WorldUnit {
  return {
    unit,
    period: spec.period,
    limit: spec.limit,
    used: 0,
    reserved: 0,
    resetAnchor: spec.period === "month" ? (spec.anchor ?? 1) : null,
    nextResetAt: spec.period === "month" ? nextMonthlyReset(spec.anchor ?? 1, now) : null,
    chargedOn: spec.charged_on,
    unitCostUsd: 0,
    estimatePerCall: 1,
  };
}

function findConnection(w: World, id: string): WorldConnection {
  const connection = w.connections.find((c) => c.id === id);
  if (!connection) throw new Rejection(`No account with id "${id}".`);
  return connection;
}

/** What the Farm's command consumer does for each kind, minus the database. Throws Rejection with the reason. */
function applyCommand(w: World, kind: CommandKind, rawPayload: JsonObject): JsonObject {
  if (!isSupportedCommand(kind)) throw new Rejection(`Command "${kind}" is not handled by this Console version.`);
  const schema = commandPayloadSchemas[kind];
  const parsed = schema.safeParse(rawPayload);
  if (!parsed.success) throw new Rejection(`Invalid payload: ${parsed.error.issues[0]?.message ?? "unknown error"}`);
  const now = Date.now();

  switch (kind) {
    case "pause": {
      const { connection_id } = commandPayloadSchemas.pause.parse(rawPayload);
      const connection = findConnection(w, connection_id);
      connection.status = "paused";
      return { ok: true, status: "paused" };
    }
    case "resume": {
      const { connection_id } = commandPayloadSchemas.resume.parse(rawPayload);
      const connection = findConnection(w, connection_id);
      if (connection.status === "needs_login") {
        throw new Rejection(`${connection.id} needs a login before it can resume. Sign in on the Farm PC first.`);
      }
      connection.status = "active";
      return { ok: true, status: "active" };
    }
    case "set_priority": {
      const { connection_id, priority } = commandPayloadSchemas.set_priority.parse(rawPayload);
      findConnection(w, connection_id).priority = priority;
      return { ok: true, priority };
    }
    case "set_strategy": {
      const { provider_id, strategy } = commandPayloadSchemas.set_strategy.parse(rawPayload);
      const provider = w.providers.find((p) => p.id === provider_id);
      if (!provider) throw new Rejection(`No pool with id "${provider_id}".`);
      provider.defaultStrategy = strategy;
      return { ok: true, strategy };
    }
    case "add_connection": {
      const spec = commandPayloadSchemas.add_connection.parse(rawPayload);
      const { provider_id } = spec;
      if (!w.providers.some((p) => p.id === provider_id)) throw new Rejection(`No pool with id "${provider_id}".`);
      if (w.connections.some((c) => c.id === spec.id))
        throw new Rejection(`An account with id "${spec.id}" already exists.`);
      w.connections.push({
        id: spec.id,
        providerId: provider_id,
        label: spec.label,
        authRef: spec.auth_ref,
        scope: spec.scope,
        priority: spec.priority,
        strategy: null,
        concurrency: spec.concurrency,
        ratePerMin: null,
        status: "active",
        plan: {
          ...spec.plan,
          renews_on: spec.plan.billing_day ? nextMonthlyReset(spec.plan.billing_day, now) : undefined,
        },
        meta: spec.meta,
        health: {
          circuit: "closed",
          consecutiveFailures: 0,
          lastErrorKind: null,
          lastError: null,
          lastErrorAt: null,
          cooldownUntil: null,
          successCount: 0,
          failureCount: 0,
          lastSuccessAt: null,
          latencyMsP50: null,
        },
        sessionsCount: 0,
        callsToday: 0,
        units: Object.entries(spec.units).map(([unit, unitSpec]) => buildUnit(unit, unitSpec, now)),
      });
      return { ok: true, connection_id: spec.id };
    }
    case "remove_connection": {
      const { connection_id } = commandPayloadSchemas.remove_connection.parse(rawPayload);
      findConnection(w, connection_id);
      w.connections = w.connections.filter((c) => c.id !== connection_id);
      return { ok: true };
    }
    case "test_connection": {
      const { connection_id } = commandPayloadSchemas.test_connection.parse(rawPayload);
      const connection = findConnection(w, connection_id);
      if (connection.health.circuit === "open") {
        throw new Rejection(
          `${connection.id} has an open circuit; the test call was blocked. Try again after the breaker half-opens.`,
        );
      }
      if (connection.status === "needs_login") {
        throw new Rejection(`${connection.id} needs a login; the test call would fail with 401.`);
      }
      connection.health.lastSuccessAt = new Date(now).toISOString();
      return { ok: true, latency_ms: connection.health.latencyMsP50 ?? 900 };
    }
    case "ack_alert": {
      const { alert_id } = commandPayloadSchemas.ack_alert.parse(rawPayload);
      const alert = w.alerts.find((a) => a.id === alert_id);
      if (!alert) throw new Rejection("Alert not found.");
      alert.acked_at ??= new Date(now).toISOString();
      return { ok: true };
    }
    case "set_budget": {
      const { scope, monthly_usd, ref, hard_stop } = commandPayloadSchemas.set_budget.parse(rawPayload);
      if (scope === "global") {
        w.globalBudgetUsd = monthly_usd;
        w.globalBudgetHardStop = hard_stop ?? true;
        return { ok: true, scope, monthly_usd, hard_stop: w.globalBudgetHardStop };
      }
      if (scope === "provider") {
        if (!ref) throw new Rejection("Provider ref required for provider budget.");
        w.providerBudgets[ref] = monthly_usd;
        w.providerBudgetHardStops ??= {};
        w.providerBudgetHardStops[ref] = hard_stop ?? true;
        return { ok: true, scope, ref, monthly_usd, hard_stop: w.providerBudgetHardStops[ref] };
      }
      return { ok: true, scope, ref, monthly_usd, hard_stop: hard_stop ?? true };
    }
    case "set_route": {
      const { capability, provider_id, position, enabled } = commandPayloadSchemas.set_route.parse(rawPayload);
      const route = w.routes.find((r) => r.capability === capability && r.providerId === provider_id);
      if (route) {
        route.position = position;
        route.enabled = enabled;
      } else {
        w.routes.push({
          capability,
          providerId: provider_id,
          position,
          enabled,
        });
      }
      return { ok: true, capability, provider_id, position, enabled };
    }
    case "update_connection": {
      const parsed = commandPayloadSchemas.update_connection.parse(rawPayload);
      const conn = findConnection(w, parsed.connection_id);
      if (parsed.label !== undefined && parsed.label !== null) conn.label = parsed.label;
      if (parsed.priority !== undefined && parsed.priority !== null) conn.priority = parsed.priority;
      if (parsed.concurrency !== undefined && parsed.concurrency !== null) conn.concurrency = parsed.concurrency;
      if (parsed.status !== undefined && parsed.status !== null) conn.status = parsed.status;
      if (parsed.strategy !== undefined) conn.strategy = parsed.strategy;
      if (parsed.auth_ref !== undefined && parsed.auth_ref !== null) conn.authRef = parsed.auth_ref;
      if (parsed.scope !== undefined && parsed.scope !== null) conn.scope = parsed.scope;
      if (parsed.meta !== undefined && parsed.meta !== null) conn.meta = { ...conn.meta, ...parsed.meta };
      return { ok: true, connection_id: parsed.connection_id };
    }
    case "cancel_ai_job": {
      const { job_id } = commandPayloadSchemas.cancel_ai_job.parse(rawPayload);
      const job = w.aiJobs.find((j) => j.id === job_id);
      if (job) {
        job.state = "cancelled";
        job.error = { kind: "cancelled", message: "Cancelled by owner via Console" };
        job.updatedAt = new Date(now).toISOString();
      }
      return { ok: true, job_id };
    }
    case "set_max_parallel": {
      const { connection_id, provider_id, max_parallel } = commandPayloadSchemas.set_max_parallel.parse(rawPayload);
      if (connection_id) {
        const conn = findConnection(w, connection_id);
        conn.concurrency = max_parallel;
        conn.meta = { ...conn.meta, max_parallel };
      }
      return { ok: true, connection_id: connection_id ?? null, provider_id: provider_id ?? null, max_parallel };
    }
    case "set_mcp_tool_access": {
      const { provider_id, tool, enabled, access } = commandPayloadSchemas.set_mcp_tool_access.parse(rawPayload);
      const isAllowed = access === "allow" || (access === undefined && enabled);
      const mcpTool = w.mcpTools.find((t) => t.provider === provider_id && t.name === tool);
      if (mcpTool) {
        mcpTool.enabled = isAllowed;
      }
      return { ok: true, provider_id, tool, enabled: isAllowed };
    }
    case "sync_mcp_tools": {
      const parsed = commandPayloadSchemas.sync_mcp_tools.parse(rawPayload);
      return { ok: true, provider_id: parsed.provider_id ?? null, synced_count: w.mcpTools.length };
    }
    case "add_provider": {
      const spec = commandPayloadSchemas.add_provider.parse(rawPayload);
      const providerId = spec.provider_id;
      const isAi = spec.kind === "ai" || Boolean(spec.cli) || spec.executor === "cli_agent";
      const isMcp = !isAi && spec.executor !== "api" && !spec.spec;
      const accountId = spec.account_id ?? spec.connection_id ?? `${providerId}-01`;

      let prov = w.providers.find((p) => p.id === providerId);
      if (!prov) {
        prov = {
          id: providerId,
          name: spec.name ?? providerId,
          kind: isAi ? "ai" : "tool",
          executor: isAi ? "cli_agent" : (spec.executor ?? "mcp"),
          defaultStrategy: spec.default_strategy,
          enabled: spec.enabled,
        };
        w.providers.push(prov);
      }

      const status = spec.status ?? (isAi || spec.auth === "oauth" ? "needs_login" : "active");
      let authRef = spec.auth_ref;
      if (!authRef) {
        if (isAi) {
          authRef = `cli:${accountId}`;
        } else if (spec.auth === "oauth") {
          authRef = `token-store:${accountId}`;
        } else {
          authRef = "cli:none";
        }
      }

      if (!w.connections.some((c) => c.id === accountId)) {
        const rawPlan = spec.plan as Record<string, unknown> | undefined;
        w.connections.push({
          id: accountId,
          providerId,
          label: spec.label ?? accountId,
          authRef,
          scope: ["internal"],
          priority: spec.priority ?? 100,
          strategy: null,
          concurrency: spec.concurrency ?? 1,
          ratePerMin: spec.rate_per_min ?? null,
          status,
          plan: {
            name: typeof rawPlan?.name === "string" ? rawPlan.name : "Default",
            price_usd: typeof rawPlan?.price_usd === "number" ? rawPlan.price_usd : 0,
          },
          meta: (spec.meta ?? {}) as Record<string, unknown>,
          health: {
            circuit: "closed",
            consecutiveFailures: 0,
            lastErrorKind: null,
            lastError: null,
            lastErrorAt: null,
            cooldownUntil: null,
            successCount: 0,
            failureCount: 0,
            lastSuccessAt: null,
            latencyMsP50: null,
          },
          sessionsCount: 0,
          callsToday: 0,
          units: [],
        });
      }

      if (isMcp) {
        const mcpCap = `mcp:${providerId}`;
        if (!w.capabilities.some((c) => c.name === mcpCap)) {
          w.capabilities.push({
            name: mcpCap,
            kind: "tool",
            description: `Pass-through MCP tools of ${providerId}`,
            defaultStrategy: "failover",
          });
        }
        if (!w.routes.some((r) => r.capability === mcpCap && r.providerId === providerId)) {
          w.routes.push({
            capability: mcpCap,
            providerId,
            position: 1,
            enabled: true,
          });
        }
        if (!w.mcpTools.some((t) => t.provider === providerId)) {
          w.mcpTools.push({
            id: `tool-${providerId}-echo`,
            workspaceId: "ws-default",
            provider: providerId,
            name: `${providerId}__echo`,
            description: `Echo tool for ${providerId}`,
            inputSchema: { type: "object", properties: { message: { type: "string" } } },
            outputSchema: null,
            annotations: null,
            schemaHash: "hash-echo",
            syncedAt: new Date(now).toISOString(),
            enabled: true,
            readOnly: true,
          });
        }
      }

      let nextStep = "farm mcp sync";
      if (isAi) {
        nextStep = `farm ai login ${accountId}`;
      } else if (spec.auth === "oauth") {
        nextStep = `farm mcp login ${accountId}`;
      }

      return {
        ok: true,
        provider_id: providerId,
        connection_id: accountId,
        status,
        next_step: nextStep,
      };
    }
    case "update_provider": {
      const spec = commandPayloadSchemas.update_provider.parse(rawPayload);
      const prov = w.providers.find((p) => p.id === spec.provider_id);
      if (!prov) throw new Rejection(`Provider "${spec.provider_id}" not found.`);
      if (spec.name !== undefined && spec.name !== null) prov.name = spec.name;
      if (spec.enabled !== undefined && spec.enabled !== null) prov.enabled = spec.enabled;
      if (spec.default_strategy !== undefined && spec.default_strategy !== null)
        prov.defaultStrategy = spec.default_strategy;
      return { ok: true, provider_id: spec.provider_id };
    }
    case "remove_provider": {
      const spec = commandPayloadSchemas.remove_provider.parse(rawPayload);
      const prov = w.providers.find((p) => p.id === spec.provider_id);
      if (!prov) throw new Rejection(`Provider "${spec.provider_id}" not found.`);
      w.providers = w.providers.filter((p) => p.id !== spec.provider_id);
      w.connections = w.connections.filter((c) => c.providerId !== spec.provider_id);
      w.routes = w.routes.filter((r) => r.providerId !== spec.provider_id);
      w.capabilities = w.capabilities.filter((c) => c.name !== `mcp:${spec.provider_id}`);
      w.mcpTools = w.mcpTools.filter((t) => t.provider !== spec.provider_id);
      return { ok: true, provider_id: spec.provider_id };
    }
    default:
      throw new Rejection(`Command "${kind}" is not handled by this Console version.`);
  }
}

function findCommand(w: World, id: string): FarmCommand | undefined {
  return w.commands.find((c) => c.id === id);
}

export class FixturesFarmData implements FarmData {
  readonly source = "fixtures" as const;

  async getOverview(): Promise<FarmOverview> {
    const w = world();
    const now = Date.now();
    return {
      generatedAt: new Date(now).toISOString(),
      pools: poolOverviewRows(w, now),
      spend: spendMonthRows(w, now),
      capacity: capabilityCapacityRows(w, now),
      runs: recentRunRows(w).slice(0, 8),
      alerts: await this.listOpenAlerts(),
      commands: await this.listCommands(6),
    };
  }

  private async listOpenAlerts(): Promise<AlertRow[]> {
    return (await this.listAlerts()).filter((alert) => alert.acked_at === null);
  }

  async listPools(kind?: ProviderKind): Promise<PoolOverviewRow[]> {
    const rows = poolOverviewRows(world());
    return kind ? rows.filter((row) => row.kind === kind) : rows;
  }

  async getPool(id: string): Promise<PoolDetail | null> {
    const w = world();
    const now = Date.now();
    const pool = poolOverviewRows(w, now).find((row) => row.provider_id === id);
    if (!pool) return null;
    return {
      pool,
      spend: spendMonthRows(w, now).find((row) => row.provider_id === id) ?? null,
      routes: capabilityCapacityRows(w, now).filter((row) => row.provider_id === id),
    };
  }

  async listConnections(poolId: string, query: ConnectionQuery = {}): Promise<Page<Connection>> {
    const { page = 1, pageSize = 10, sort = "priority", dir = "asc" } = query;
    const all = groupConnectionRows(connectionStatusRows(world()).filter((row) => row.provider_id === poolId));
    const factor = dir === "desc" ? -1 : 1;
    const compare: Record<ConnectionSortKey, (a: Connection, b: Connection) => number> = {
      label: (a, b) => a.label.localeCompare(b.label),
      status: (a, b) => a.status.localeCompare(b.status),
      priority: (a, b) => a.priority - b.priority,
    };
    all.sort((a, b) => factor * compare[sort](a, b) || a.id.localeCompare(b.id));
    const start = (page - 1) * pageSize;
    return { items: all.slice(start, start + pageSize), total: all.length, page, pageSize };
  }

  async getConnection(id: string): Promise<Connection | null> {
    const rows = connectionStatusRows(world()).filter((row) => row.connection_id === id);
    return groupConnectionRows(rows)[0] ?? null;
  }

  async listRecentRuns(n = 8): Promise<RunRow[]> {
    return recentRunRows(world()).slice(0, n);
  }

  async getRun(id: string): Promise<RunRow | null> {
    return recentRunRows(world()).find((run) => run.id === id) ?? null;
  }

  async listAlerts(): Promise<AlertRow[]> {
    return [...world().alerts]
      .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] || b.created_at.localeCompare(a.created_at))
      .map((alert) => ({ ...alert }));
  }

  async listCommands(n = 20): Promise<FarmCommand[]> {
    return [...world().commands]
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, n)
      .map((command) => ({ ...command }));
  }

  async getCommand(id: string): Promise<FarmCommand | null> {
    const command = findCommand(world(), id);
    return command ? { ...command } : null;
  }

  async enqueueCommand(kind: CommandKind, payload: JsonObject): Promise<FarmCommand> {
    const w = world();
    const command: FarmCommand = {
      id: crypto.randomUUID(),
      kind,
      payload,
      status: "queued",
      result: null,
      created_by: w.ownerEmail,
      created_at: new Date().toISOString(),
      done_at: null,
    };
    w.commands.unshift(command);

    setTimeout(() => {
      command.status = "running";
    }, QUEUE_RUNNING_MS);

    setTimeout(() => {
      try {
        command.result = applyCommand(world(), kind, payload);
        command.status = "done";
      } catch (error) {
        command.status = "rejected";
        command.result = { ok: false, reason: error instanceof Rejection ? error.message : "Command failed." };
      }
      command.done_at = new Date().toISOString();
    }, QUEUE_DONE_MS);

    return { ...command };
  }

  async getBillingOverview(dailyDays = 90): Promise<BillingOverview> {
    const w = world();
    const now = Date.now();
    const renewals = renewalRows(w, 45, now);
    const idlePaid = idlePaidRows(w, now);
    const paidAccounts = w.connections.filter((c) => (c.plan.price_usd ?? 0) > 0 && c.status !== "disabled");

    return {
      spendMonth: spendMonthRows(w, now),
      spendDaily: spendDailyRows(w, dailyDays, now),
      budgets: budgetRows(w, now),
      renewals,
      costPerResult: costPerResultRows(w, now),
      idlePaid,
      paidAccountsCount: paidAccounts.length,
      idlePaidCount: idlePaid.length,
    };
  }

  async listSpendDaily(days = 90): Promise<SpendDailyRow[]> {
    return spendDailyRows(world(), days);
  }

  async listBudgets(): Promise<BudgetRow[]> {
    return budgetRows(world());
  }

  async listRenewals(days = 45): Promise<RenewalRow[]> {
    return renewalRows(world(), days);
  }

  async listCostPerResult(): Promise<CostPerResultRow[]> {
    return costPerResultRows(world());
  }

  async listIdlePaid(): Promise<IdlePaidRow[]> {
    return idlePaidRows(world());
  }

  async listRuns(query: RunQuery = {}): Promise<Page<RunRow>> {
    const { page = 1, pageSize = 20, capability, status, failuresOnly, caller, from, to } = query;
    let all = recentRunRows(world());

    if (capability) {
      all = all.filter((r) => r.capability === capability);
    }
    if (status) {
      all = all.filter((r) => r.status === status);
    }
    if (failuresOnly) {
      all = all.filter((r) => r.status === "failed" || r.status === "blocked");
    }
    if (caller) {
      all = all.filter((r) => r.caller === caller);
    }
    if (from) {
      all = all.filter((r) => r.started_at >= from);
    }
    if (to) {
      all = all.filter((r) => r.started_at <= to);
    }

    const start = (page - 1) * pageSize;
    return {
      items: all.slice(start, start + pageSize),
      total: all.length,
      page,
      pageSize,
    };
  }

  async getRunDetail(id: string): Promise<RunDetailRow | null> {
    return runDetail(world(), id);
  }

  // C3: Routing, Memory & Evidence, Policies & Audits
  async listRoutes(capability?: string): Promise<RouteRow[]> {
    const w = world();
    const rows = routesRows(w, Date.now());
    return capability ? rows.filter((r) => r.capability === capability) : rows;
  }

  async listFacts(query?: FactQuery): Promise<Page<FactRow>> {
    const w = world();
    let rows = factsRows(w, Date.now());
    if (query?.freshness && query.freshness !== "all") {
      rows = rows.filter((r) => r.freshness_state === query.freshness);
    }
    if (query?.entityKind) {
      rows = rows.filter((r) => r.entity_kind === query.entityKind);
    }
    if (query?.search) {
      const q = query.search.toLowerCase();
      rows = rows.filter(
        (r) =>
          r.entity_name.toLowerCase().includes(q) ||
          r.entity_canonical_key.toLowerCase().includes(q) ||
          r.attribute.toLowerCase().includes(q),
      );
    }
    const page = query?.page ?? 1;
    const pageSize = query?.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    return {
      items: rows.slice(start, start + pageSize),
      total: rows.length,
      page,
      pageSize,
    };
  }

  async getFact(id: string): Promise<FactRow | null> {
    const w = world();
    return factsRows(w, Date.now()).find((f) => f.id === id) ?? null;
  }

  async listEvidence(query?: EvidenceQuery): Promise<Page<EvidenceRow>> {
    const w = world();
    let rows = evidenceRows(w);
    if (query?.search) {
      const q = query.search.toLowerCase();
      rows = rows.filter(
        (r) =>
          r.sha256.toLowerCase().includes(q) || r.url?.toLowerCase().includes(q) || r.path.toLowerCase().includes(q),
      );
    }
    const page = query?.page ?? 1;
    const pageSize = query?.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    return {
      items: rows.slice(start, start + pageSize),
      total: rows.length,
      page,
      pageSize,
    };
  }

  async getEvidence(id: string): Promise<EvidenceRow | null> {
    const w = world();
    return evidenceRows(w).find((e) => e.id === id) ?? null;
  }

  async listAuditEvents(query?: AuditQuery): Promise<Page<AuditEventRow>> {
    const w = world();
    let rows = auditEventRows(w);
    if (query?.actor) {
      const actor = query.actor;
      rows = rows.filter((r) => r.actor.includes(actor));
    }
    if (query?.action) {
      rows = rows.filter((r) => r.action === query.action);
    }
    const page = query?.page ?? 1;
    const pageSize = query?.pageSize ?? 20;
    const start = (page - 1) * pageSize;
    return {
      items: rows.slice(start, start + pageSize),
      total: rows.length,
      page,
      pageSize,
    };
  }
}
