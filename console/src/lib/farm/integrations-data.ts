// =====================================================================================================================
// Integrations & AI Orchestration Data Layer (C3 Brief / OPEN1 / AIP2).
//
// SINGLE ACCESS MODULE for reads/writes of:
//   - OPEN1 tables: `mcp_tools`, MCP provider config, tool allow/deny
//   - AIP2 tables: `ai_jobs`, `ai_conversations`, account concurrency (max_parallel)
//
// Build against fixtures now; the lead reconciles table names with OPEN1/AIP2 migrations at merge.
// =====================================================================================================================

import { getFarmData } from "./data";
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
  toolCount: number;
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
  enabled: boolean;
  readOnly: boolean;
}

export interface McpDetail {
  integration: IntegrationSummary;
  tools: McpToolItem[];
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
  task: string;
  ai: string;
  account: string;
  model: string;
  mode: "answer" | "edit";
  cwd: string | null;
  conversationId: string | null;
  jsonSchema: JsonObject | null;
  timeoutS: number;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  elapsedS: number;
  tokens: number;
  costUsd: number;
  nativeSessionId: string | null;
  error: { kind: string; message: string; retry_at?: string } | null;
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

    let toolCount = 0;
    let transport = "stdio";
    const namespace = pool.provider_id;
    let exposeMode: "direct" | "discovery" | "auto" | "n/a" = "direct";
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
          config = { cli: pool.provider_id, models: accounts[0]?.id ? ["default"] : [] };
        }
      }
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
    cliCommands,
  };
}

// ---------------------------------------------------------------------------
// AIP2: AI Jobs & Conversations
// ---------------------------------------------------------------------------

export async function getAiJobsList(filter?: { state?: string; ai?: string; search?: string }): Promise<AiJobItem[]> {
  const farmData = await getFarmData();
  const { FixturesFarmData } = await import("./fixtures/source");
  if (!(farmData instanceof FixturesFarmData)) {
    return [];
  }

  const { buildWorld } = await import("./fixtures/world");
  const store = globalThis as unknown as { __farmFixtureWorld?: ReturnType<typeof buildWorld> };
  const w = store.__farmFixtureWorld;
  if (!w) return [];

  let jobs = w.aiJobs.map((j) => ({
    id: j.id,
    task: j.task,
    ai: j.ai,
    account: j.account,
    model: j.model,
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
        j.task.toLowerCase().includes(q) ||
        j.id.toLowerCase().includes(q) ||
        j.account.toLowerCase().includes(q) ||
        j.model.toLowerCase().includes(q),
    );
  }

  return jobs;
}

export async function getAiConversationsList(filter?: { ai?: string; search?: string }): Promise<AiConversationItem[]> {
  const farmData = await getFarmData();
  const { FixturesFarmData } = await import("./fixtures/source");
  if (!(farmData instanceof FixturesFarmData)) {
    return [];
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

