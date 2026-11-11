// =====================================================================================================================
// Integrations & AI Orchestration Data Layer (C3 Brief / OPEN1 / AIP2).
//
// SINGLE ACCESS MODULE for reads/writes of:
//   - OPEN1 tables: `mcp_tools`, MCP provider config, tool allow/deny
//   - AIP2 tables: `ai_jobs`, `ai_conversations`, account concurrency (max_parallel)
//
// Build against fixtures now; the lead reconciles table names with OPEN1/AIP2 migrations at merge.
// =====================================================================================================================

import { createClient } from "@/lib/supabase/server";

import { getFarmData } from "./data";
import { mcpToolAllowed } from "./mcp-permissions";
import { getTenantId } from "./seal";
import type { ConnectionStatus, JsonObject, PoolHealth, ProviderExecutor } from "./types";

export interface IntegrationAccount {
  id: string;
  label: string;
  authRef: string;
  status: ConnectionStatus;
  priority: number;
  concurrency: number;
  lastSuccessAt: string | null;
  planName?: string;
}

export interface IntegrationSummary {
  id: string;
  name: string;
  kind: "mcp" | "openapi" | "ai";
  executor: ProviderExecutor;
  transport: string;
  namespace: string;
  exposeMode: "direct" | "discovery" | "auto" | "n/a";
  health: PoolHealth;
  /** Tools in the synced catalogue (`mcp_tools`); null when it is unknown (not synced yet, or not readable). */
  toolCount: number | null;
  accountsCount: number;
  accounts: IntegrationAccount[];
  lastSyncAt: string | null;
  config: JsonObject;
}

export interface McpToolItem {
  id: string;
  provider: string;
  name: string;
  description: string;
  inputSchema: JsonObject;
  outputSchema: JsonObject | null;
  annotations: JsonObject | null;
  schemaHash: string;
  syncedAt: string;
  enabled: boolean | null;
  readOnly: boolean;
}

export interface McpDetail {
  integration: IntegrationSummary;
  tools: McpToolItem[];
  toolsTruncated?: boolean;
  cliCommands: {
    importCmd: string;
    loginCmd: string;
    secretCmd: string;
    syncCmd: string;
    testCmd: string;
  };
}

export interface AiJobItem {
  id: string;
  /** Null when read from `v_ai_jobs`, which keeps task input, cwd, schema, session id and error text out of the Console. */
  task: string | null;
  ai: string;
  account: string;
  operatorEmail?: string | null;
  operatorClient?: string | null;
  /** The requested model; null when the job named none. */
  model: string | null;
  /** The exact model the worker reported; null = unknown. */
  resolvedModel: string | null;
  requestedEffort: string | null;
  /** What the driver applied (e.g. Codex caps xhigh/max at high); null = unknown. */
  effectiveEffort: string | null;
  /** How the choice was made; null on older jobs = unknown. */
  selectionSource: "explicit" | "account_defaults" | "inherited" | null;
  mode: "answer" | "edit";
  cwd: string | null;
  conversationId: string | null;
  jsonSchema: JsonObject | null;
  timeoutS: number | null;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  elapsedS: number;
  tokens: number;
  costUsd: number;
  nativeSessionId: string | null;
  error: { kind: string; message?: string; retry_at?: string } | null;
  createdAt: string;
  updatedAt: string;
}

export interface AiConversationItem {
  id: string;
  ai: string;
  account: string;
  nativeSessionId: string;
  turns: number;
  tokens: number;
  costUsd: number;
  lastJobId: string;
  createdAt: string;
  updatedAt: string;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/**
 * Tools per provider in `mcp_tools` (written by `farm mcp sync`; owner-readable under RLS). Returns null when the
 * table cannot be read, so the cards show "—" instead of a false 0. A provider missing from the map has no rows.
 */
async function countSyncedTools(providers: string[]): Promise<Map<string, number> | null> {
  try {
    const supabase = await createClient();
    const counts = new Map<string, number>();
    for (const provider of providers) {
      const result = await supabase
        .from("mcp_tools")
        .select("provider", { head: true, count: "exact" })
        .eq("tenant_id", getTenantId())
        .eq("provider", provider);
      if (result.error || result.count === null) return null;
      counts.set(provider, result.count);
    }
    return counts;
  } catch {
    return null;
  }
}

export async function getIntegrationsList(filter?: {
  kind?: "all" | "mcp" | "openapi" | "ai";
  health?: "all" | "healthy" | "degraded" | "down";
  search?: string;
}): Promise<IntegrationSummary[]> {
  const farmData = await getFarmData();
  const pools = await farmData.listPools();

  // Load fixture world or Supabase data
  const { FixturesFarmData } = await import("./fixtures/source");
  const isFixtures = farmData instanceof FixturesFarmData;

  const syncedTools = isFixtures
    ? null
    : await countSyncedTools(pools.filter((p) => p.executor === "mcp").map((p) => p.provider_id));
  const configs = new Map<string, JsonObject>();
  if (!isFixtures) {
    const db = await createClient();
    const response = await db.from("providers").select("id,config").eq("tenant_id", getTenantId());
    if (response.error) throw new Error("Could not read integration settings");
    for (const row of response.data ?? []) configs.set(row.id, row.config as JsonObject);
  }
  const results: IntegrationSummary[] = [];

  for (const pool of pools) {
    const isMcp = pool.executor === "mcp";
    const isOpenApi = pool.executor === "api" && pool.provider_id === "resend";
    const isAi = pool.kind === "ai";

    let kind: "mcp" | "openapi" | "ai" = "mcp";
    if (isAi) kind = "ai";
    else if (isOpenApi) kind = "openapi";
    else if (isMcp) kind = "mcp";
    else continue; // GTM tools without direct integration view

    // Get accounts for this pool
    const accountsPage = await farmData.listConnections(pool.provider_id, { pageSize: 50 });
    const accounts: IntegrationAccount[] = accountsPage.items.map((acc) => ({
      id: acc.id,
      label: acc.label,
      authRef: acc.authRef ?? "",
      status: acc.status,
      priority: acc.priority,
      concurrency: acc.concurrency,
      lastSuccessAt: acc.health.lastSuccessAt,
      planName: acc.plan.name,
    }));

    // Fixtures count the fixture world below. Live: the synced catalogue, or null (unknown) when there is none yet.
    let toolCount: number | null = isFixtures ? 0 : null;
    if (!isFixtures && isMcp) {
      const synced = syncedTools?.get(pool.provider_id) ?? 0;
      toolCount = synced > 0 ? synced : null;
    }
    let transport = isFixtures ? "stdio" : "unknown";
    let namespace = pool.provider_id;
    let exposeMode: "direct" | "discovery" | "auto" | "n/a" = isFixtures ? "direct" : "n/a";
    let lastSyncAt: string | null = null;
    let config: JsonObject = {};

    if (isFixtures) {
      const { buildWorld } = await import("./fixtures/world");
      // Access singleton store if available
      const store = globalThis as unknown as { __farmFixtureWorld?: ReturnType<typeof buildWorld> };
      const w = store.__farmFixtureWorld;
      if (w) {
        const tools = w.mcpTools.filter((t) => t.provider === pool.provider_id);
        toolCount = tools.length;
        if (toolCount > 0) lastSyncAt = tools[0].syncedAt;
        if (pool.provider_id === "notion") {
          transport = "http";
          exposeMode = "discovery";
          config = { transport: "http", url: "https://api.notion.com/v1/mcp", auth: "oauth", namespace: "notion" };
        } else if (pool.provider_id === "github") {
          transport = "stdio";
          exposeMode = "direct";
          config = {
            transport: "stdio",
            command: "github-mcp-server",
            args: ["--stdio"],
            auth: "env",
            namespace: "github",
          };
        } else if (pool.provider_id === "linear") {
          transport = "http";
          exposeMode = "auto";
          config = { transport: "http", url: "https://mcp.linear.app", auth: "oauth", namespace: "linear" };
        } else if (pool.provider_id === "local-tools") {
          transport = "stdio";
          exposeMode = "direct";
          config = { transport: "stdio", command: "node ./tools/mcp-server.js", auth: "none", namespace: "local" };
        } else if (pool.provider_id === "clay") {
          transport = "http";
          exposeMode = "direct";
          config = { transport: "http", auth: "oauth", namespace: "clay" };
        } else if (isOpenApi) {
          transport = "https";
          exposeMode = "n/a";
          config = {
            openapi_spec: "https://api.resend.com/openapi.json",
            auth: "env:RESEND_API_KEY",
            namespace: "resend",
          };
        } else if (isAi) {
          transport = "cli";
          exposeMode = "n/a";
          config = { cli: pool.provider_id, models: [] };
        }
      }
    }

    if (!isFixtures) {
      const block = configs.get(pool.provider_id)?.mcp;
      if (block && typeof block === "object" && !Array.isArray(block)) {
        const mcp = block as JsonObject;
        if (typeof mcp.transport === "string") transport = mcp.transport;
        if (typeof mcp.namespace === "string") namespace = mcp.namespace;
        if (mcp.expose === "auto" || mcp.expose === "direct" || mcp.expose === "discovery") exposeMode = mcp.expose;
        const tools = mcp.tools;
        if (tools && typeof tools === "object" && !Array.isArray(tools)) {
          const rules = tools as JsonObject;
          config = { tools: { allow: rules.allow ?? ["*"], deny: rules.deny ?? [] } };
        }
      } else if (isAi) transport = "cli";
    }
    results.push({
      id: pool.provider_id,
      name: pool.provider_name,
      kind,
      executor: pool.executor,
      transport,
      namespace,
      exposeMode,
      health: pool.health,
      toolCount,
      accountsCount: pool.accounts_total,
      accounts,
      lastSyncAt,
      config,
    });
  }

  // Apply filters
  let filtered = results;
  if (filter?.kind && filter.kind !== "all") {
    filtered = filtered.filter((r) => r.kind === filter.kind);
  }
  if (filter?.health && filter.health !== "all") {
    filtered = filtered.filter((r) => r.health === filter.health);
  }
  if (filter?.search) {
    const q = filter.search.toLowerCase();
    filtered = filtered.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q) ||
        r.namespace.toLowerCase().includes(q) ||
        r.accounts.some((a) => a.label.toLowerCase().includes(q) || a.id.toLowerCase().includes(q)),
    );
  }

  return filtered;
}

export async function getMcpServerDetail(providerId: string): Promise<McpDetail | null> {
  const integrations = await getIntegrationsList();
  const integration = integrations.find((i) => i.id === providerId);
  if (!integration) return null;

  const farmData = await getFarmData();
  const { FixturesFarmData } = await import("./fixtures/source");
  const isFixtures = farmData instanceof FixturesFarmData;

  let tools: McpToolItem[] = [];
  let toolsTruncated = false;

  if (isFixtures) {
    const { buildWorld } = await import("./fixtures/world");
    const store = globalThis as unknown as { __farmFixtureWorld?: ReturnType<typeof buildWorld> };
    const w = store.__farmFixtureWorld;
    if (w) {
      tools = w.mcpTools
        .filter((t) => t.provider === providerId)
        .map((t) => ({
          id: t.id,
          provider: t.provider,
          name: t.name,
          description: t.description,
          inputSchema: t.inputSchema,
          outputSchema: t.outputSchema,
          annotations: t.annotations,
          schemaHash: t.schemaHash,
          syncedAt: t.syncedAt,
          enabled: t.enabled,
          readOnly: t.readOnly,
        }));
    }
  } else {
    const db = await createClient();
    const result = await db
      .from("mcp_tools")
      .select("name,description,input_schema,output_schema,annotations,schema_hash,synced_at")
      .eq("tenant_id", getTenantId())
      .eq("provider", providerId)
      .order("name")
      .limit(1000);
    if (result.error) throw new Error("Could not read this MCP catalogue");
    tools = (result.data ?? []).map((row) => ({
      id: `${providerId}:${row.name}`,
      provider: providerId,
      name: row.name,
      description: row.description ?? "",
      inputSchema: row.input_schema as JsonObject,
      outputSchema: row.output_schema as JsonObject | null,
      annotations: row.annotations as JsonObject | null,
      schemaHash: row.schema_hash,
      syncedAt: row.synced_at,
      readOnly: row.annotations?.readOnlyHint === true,
      enabled: mcpToolAllowed(integration.config.tools, row.name),
    }));
    toolsTruncated = integration.toolCount !== null && integration.toolCount > tools.length;
    if (tools.length) integration.lastSyncAt = tools[0].syncedAt;
  }

  const cliCommands = {
    importCmd: `farm mcp import --from claude-desktop --only ${providerId}`,
    loginCmd: `farm mcp login ${providerId}-01`,
    secretCmd: `farm set-secret ${providerId.toUpperCase().replace(/-/g, "_")}_TOKEN`,
    syncCmd: `farm mcp sync ${providerId}`,
    testCmd: `farm test connection ${providerId}-01`,
  };

  return {
    integration,
    tools,
    toolsTruncated,
    cliCommands,
  };
}

// ---------------------------------------------------------------------------
// AIP2: AI Jobs & Conversations
// ---------------------------------------------------------------------------

const AI_JOB_COLUMNS =
  "id, conversation_id, ai, account, model, resolved_model, requested_effort, effective_effort, selection_source, " +
  "mode, state, tokens, cost_usd, duration_s, error_kind, created_at, updated_at, operator_email, operator_client";

interface AiJobRow {
  id: string;
  conversation_id: string | null;
  ai: string;
  account: string;
  operator_email: string | null;
  operator_client: string | null;
  model: string | null;
  resolved_model: string | null;
  requested_effort: string | null;
  effective_effort: string | null;
  selection_source: AiJobItem["selectionSource"];
  mode: AiJobItem["mode"];
  state: AiJobItem["state"];
  tokens: number | null;
  cost_usd: number | string;
  duration_s: number | string | null;
  error_kind: string | null;
  created_at: string;
  updated_at: string;
}

/** Latest jobs through the signed-in user's client: the invoker view applies their RLS, the server picks the tenant. */
async function readAiJobs(): Promise<AiJobItem[]> {
  const supabase = await createClient();
  const result = await supabase
    .from("v_ai_jobs")
    .select(AI_JOB_COLUMNS)
    .eq("tenant_id", getTenantId())
    .order("created_at", { ascending: false })
    .limit(200);
  if (result.error) throw new Error(`Could not read AI jobs: ${result.error.message}`);
  return ((result.data ?? []) as unknown as AiJobRow[]).map((row) => ({
    id: row.id,
    task: null,
    ai: row.ai,
    account: row.account,
    operatorEmail: row.operator_email,
    operatorClient: row.operator_client,
    model: row.model,
    resolvedModel: row.resolved_model,
    requestedEffort: row.requested_effort,
    effectiveEffort: row.effective_effort,
    selectionSource: row.selection_source,
    mode: row.mode,
    cwd: null,
    conversationId: row.conversation_id,
    jsonSchema: null,
    timeoutS: null,
    state: row.state,
    elapsedS: Number(row.duration_s ?? 0),
    tokens: Number(row.tokens ?? 0),
    costUsd: Number(row.cost_usd),
    nativeSessionId: null,
    error: row.error_kind ? { kind: row.error_kind } : null,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  }));
}

async function readFixtureAiJobs(): Promise<AiJobItem[]> {
  const { buildWorld } = await import("./fixtures/world");
  const store = globalThis as unknown as { __farmFixtureWorld?: ReturnType<typeof buildWorld> };
  const w = store.__farmFixtureWorld;
  if (!w) return [];

  return w.aiJobs.map((j) => ({
    id: j.id,
    task: j.task,
    ai: j.ai,
    account: j.account,
    model: j.model,
    resolvedModel: null,
    requestedEffort: null,
    effectiveEffort: null,
    selectionSource: null,
    mode: j.mode,
    cwd: j.cwd,
    conversationId: j.conversationId,
    jsonSchema: j.jsonSchema,
    timeoutS: j.timeoutS,
    state: j.state,
    elapsedS: j.elapsedS,
    tokens: j.tokens,
    costUsd: j.costUsd,
    nativeSessionId: j.nativeSessionId,
    error: j.error,
    createdAt: j.createdAt,
    updatedAt: j.updatedAt,
  }));
}

export async function getAiJobsList(filter?: { state?: string; ai?: string; search?: string }): Promise<AiJobItem[]> {
  const farmData = await getFarmData();
  const { FixturesFarmData } = await import("./fixtures/source");
  let jobs = farmData instanceof FixturesFarmData ? await readFixtureAiJobs() : await readAiJobs();

  if (filter?.state && filter.state !== "all") {
    jobs = jobs.filter((j) => j.state === filter.state);
  }
  if (filter?.ai && filter.ai !== "all") {
    jobs = jobs.filter((j) => j.ai === filter.ai);
  }
  if (filter?.search) {
    const q = filter.search.toLowerCase();
    jobs = jobs.filter(
      (j) =>
        j.task?.toLowerCase().includes(q) ||
        j.id.toLowerCase().includes(q) ||
        j.account.toLowerCase().includes(q) ||
        j.model?.toLowerCase().includes(q) ||
        j.resolvedModel?.toLowerCase().includes(q),
    );
  }

  return jobs;
}

export async function getAiConversationsList(filter?: { ai?: string; search?: string }): Promise<AiConversationItem[]> {
  const farmData = await getFarmData();
  const { FixturesFarmData } = await import("./fixtures/source");
  if (!(farmData instanceof FixturesFarmData)) {
    const db = await createClient();
    let query = db
      .from("ai_conversations")
      .select("id,ai,account,native_session_id,turns,tokens,cost,last_job_id,created_at,updated_at")
      .eq("tenant_id", getTenantId())
      .order("updated_at", { ascending: false })
      .limit(200);
    if (filter?.ai && filter.ai !== "all") query = query.eq("ai", filter.ai);
    const result = await query;
    if (result.error) throw new Error(`Could not read AI conversations: ${result.error.message}`);
    const rows: AiConversationItem[] = (result.data ?? []).map((row) => ({
      id: String(row.id),
      ai: String(row.ai),
      account: String(row.account),
      nativeSessionId: row.native_session_id ?? "",
      turns: Number(row.turns),
      tokens: Number(row.tokens),
      costUsd: Number(row.cost),
      lastJobId: row.last_job_id ?? "",
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
    }));
    const search = filter?.search?.toLowerCase();
    return search
      ? rows.filter((row) =>
          [row.id, row.account, row.nativeSessionId].some((value) => value.toLowerCase().includes(search)),
        )
      : rows;
  }

  const { buildWorld } = await import("./fixtures/world");
  const store = globalThis as unknown as { __farmFixtureWorld?: ReturnType<typeof buildWorld> };
  const w = store.__farmFixtureWorld;
  if (!w) return [];

  let convs = w.aiConversations.map((c) => ({
    id: c.id,
    ai: c.ai,
    account: c.account,
    nativeSessionId: c.nativeSessionId,
    turns: c.turns,
    tokens: c.tokens,
    costUsd: c.costUsd,
    lastJobId: c.lastJobId,
    createdAt: c.createdAt,
    updatedAt: c.updatedAt,
  }));

  if (filter?.ai && filter.ai !== "all") {
    convs = convs.filter((c) => c.ai === filter.ai);
  }
  if (filter?.search) {
    const q = filter.search.toLowerCase();
    convs = convs.filter(
      (c) =>
        c.id.toLowerCase().includes(q) ||
        c.account.toLowerCase().includes(q) ||
        c.nativeSessionId.toLowerCase().includes(q),
    );
  }

  return convs;
}
