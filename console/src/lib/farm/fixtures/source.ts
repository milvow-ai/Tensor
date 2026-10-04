// Fixtures data source: the fixture world behind the same FarmData interface as Supabase.
// Commands go into an in-memory queue; after ~0.5 s a command is "running" and after 1.5 s "done" (or "rejected"
// with a reason), and only then does the world change, the way the real Farm consumer behaves.

import { type ConnectionSpecPayload, commandPayloadSchemas, isSupportedCommand } from "../commands";
import { groupConnectionRows } from "../group";
import type {
  AlertRow,
  CommandKind,
  Connection,
  ConnectionQuery,
  ConnectionSortKey,
  FarmCommand,
  FarmData,
  FarmOverview,
  JsonObject,
  Page,
  PoolDetail,
  PoolOverviewRow,
  ProviderKind,
  RunRow,
} from "../types";
import { capabilityCapacityRows, connectionStatusRows, poolOverviewRows, recentRunRows, spendMonthRows } from "./views";
import { buildWorld, nextMonthlyReset, type World, type WorldConnection, type WorldUnit } from "./world";

const QUEUE_RUNNING_MS = 500;
const QUEUE_DONE_MS = 1500;

const store = globalThis as unknown as { __farmFixtureWorld?: World };

function world(): World {
  store.__farmFixtureWorld ??= buildWorld();
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
}
