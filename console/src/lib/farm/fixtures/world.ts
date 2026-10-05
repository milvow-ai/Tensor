// Fixture "world": the Harness Farm as it would look mid-month, expressed as table-shaped data
// (providers, connections, units, health, routes, runs, alerts, commands). `views.ts` derives the five `v_*`
// view rows from it with the same rules as console/sql/views.sql, so the UI sees the same shapes in both modes.
//
// All timestamps are relative to the moment the world is built, so relative times and cooldown countdowns look
// right whenever the Console starts. Development only; the data source guard refuses to load this in production.

import type {
  AlertRow,
  ChargedOn,
  CircuitState,
  ConnectionMeta,
  ConnectionStatus,
  FarmCommand,
  PeriodUnit,
  PlanInfo,
  ProviderExecutor,
  ProviderKind,
  RunRow,
} from "../types";

export interface WorldUnit {
  unit: string;
  period: PeriodUnit;
  limit: number | null;
  used: number;
  reserved: number;
  resetAnchor: number | null;
  nextResetAt: string | null;
  chargedOn: ChargedOn;
  unitCostUsd: number;
  estimatePerCall: number;
}

export interface WorldHealth {
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
}

export interface WorldConnection {
  id: string;
  providerId: string;
  label: string;
  authRef: string;
  scope: string[];
  priority: number;
  strategy: string | null;
  concurrency: number;
  ratePerMin: number | null;
  status: ConnectionStatus;
  plan: PlanInfo;
  meta: ConnectionMeta;
  health: WorldHealth;
  sessionsCount: number;
  callsToday: number;
  units: WorldUnit[];
}

export interface WorldProvider {
  id: string;
  name: string;
  kind: ProviderKind;
  executor: ProviderExecutor;
  defaultStrategy: string;
  enabled: boolean;
}

export interface WorldCapability {
  name: string;
  kind: ProviderKind;
  description: string;
  defaultStrategy: string;
}

export interface WorldRoute {
  capability: string;
  providerId: string;
  position: number;
  enabled: boolean;
}

export interface WorldSpend {
  usageUsd: number;
  billingUsd: number;
}

export interface WorldEntity {
  id: string;
  kind: string;
  canonicalKey: string;
  name: string;
}

export interface WorldFact {
  id: string;
  entityId: string;
  attribute: string;
  value: unknown;
  sourceConnectionId: string | null;
  observedAt: string;
  expiresAt: string | null;
  confidence: number;
  evidenceIds: string[];
}

export interface WorldEvidence {
  id: string;
  sha256: string;
  path: string;
  url: string | null;
  thumbPath: string | null;
  capturedAt: string;
  toolVersion: string | null;
  robotsDecision: string;
}

export interface WorldAuditEvent {
  id: number;
  actor: string;
  action: string;
  target: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  at: string;
}

export interface WorldMcpTool {
  id: string;
  workspaceId: string;
  provider: string;
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  outputSchema: Record<string, unknown> | null;
  annotations: Record<string, unknown> | null;
  schemaHash: string;
  syncedAt: string;
  enabled: boolean;
  readOnly: boolean;
}

export interface WorldAiJob {
  id: string;
  task: string;
  ai: string;
  account: string;
  model: string;
  mode: "answer" | "edit";
  cwd: string | null;
  conversationId: string | null;
  jsonSchema: Record<string, unknown> | null;
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

export interface WorldAiConversation {
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

export interface World {
  builtAt: number;
  ownerEmail: string;
  providers: WorldProvider[];
  connections: WorldConnection[];
  capabilities: WorldCapability[];
  routes: WorldRoute[];
  runs: RunRow[];
  alerts: AlertRow[];
  commands: FarmCommand[];
  providerBudgets: Record<string, number | undefined>;
  providerBudgetHardStops: Record<string, boolean>;
  globalBudgetUsd: number;
  globalBudgetHardStop: boolean;
  spend: Record<string, WorldSpend | undefined>;
  entities: WorldEntity[];
  facts: WorldFact[];
  evidence: WorldEvidence[];
  auditEvents: WorldAuditEvent[];
  mcpTools: WorldMcpTool[];
  aiJobs: WorldAiJob[];
  aiConversations: WorldAiConversation[];
}

// ---------------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------------
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

/** Next 00:00 UTC on `anchor` day of month strictly after `now` (anchor clamped to the month length). */
export function nextMonthlyReset(anchor: number, now: number): string {
  const base = new Date(now);
  for (let offset = 0; offset < 3; offset++) {
    const year = base.getUTCFullYear();
    const month = base.getUTCMonth() + offset;
    const daysInMonth = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
    const candidate = Date.UTC(year, month, Math.min(anchor, daysInMonth));
    if (candidate > now) return iso(candidate);
  }
  return iso(now + 30 * DAY);
}

function nextDayReset(now: number): string {
  return iso(Math.floor(now / DAY) * DAY + DAY);
}

// Deterministic pseudo-random numbers so fixtures never flicker between reloads.
function mulberry32(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------------------
// Seed
// ---------------------------------------------------------------------------
interface UnitSeed {
  unit: string;
  period: PeriodUnit;
  limit: number | null;
  used: number;
  reserved?: number;
  anchor?: number;
  chargedOn?: ChargedOn;
  cost?: number;
  est?: number;
  resetInMs?: number;
}

interface ConnSeed {
  id: string;
  providerId: string;
  label: string;
  authRef: string;
  priority: number;
  status?: ConnectionStatus;
  concurrency?: number;
  ratePerMin?: number | null;
  plan: { name: string; price: number; day: number };
  meta?: ConnectionMeta;
  units: UnitSeed[];
  success: number;
  failure: number;
  p50: number | null;
  lastSuccessMinAgo: number | null;
  sessions?: number;
  callsToday?: number;
  health?: Partial<WorldHealth>;
}

function buildUnit(seed: UnitSeed, now: number): WorldUnit {
  let nextResetAt: string | null = null;
  if (seed.resetInMs !== undefined) nextResetAt = iso(now + seed.resetInMs);
  else if (seed.period === "month") nextResetAt = nextMonthlyReset(seed.anchor ?? 1, now);
  else if (seed.period === "day") nextResetAt = nextDayReset(now);
  else if (seed.period === "week") nextResetAt = iso(now + 3 * DAY + 5 * HOUR);
  return {
    unit: seed.unit,
    period: seed.period,
    limit: seed.limit,
    used: seed.used,
    reserved: seed.reserved ?? 0,
    resetAnchor: seed.period === "month" ? (seed.anchor ?? 1) : null,
    nextResetAt,
    chargedOn: seed.chargedOn ?? "attempt",
    unitCostUsd: seed.cost ?? 0,
    estimatePerCall: seed.est ?? 1,
  };
}

function buildConnection(seed: ConnSeed, now: number): WorldConnection {
  const health: WorldHealth = {
    circuit: "closed",
    consecutiveFailures: 0,
    lastErrorKind: null,
    lastError: null,
    lastErrorAt: null,
    cooldownUntil: null,
    successCount: seed.success,
    failureCount: seed.failure,
    lastSuccessAt: seed.lastSuccessMinAgo === null ? null : iso(now - seed.lastSuccessMinAgo * MINUTE),
    latencyMsP50: seed.p50,
    ...seed.health,
  };
  return {
    id: seed.id,
    providerId: seed.providerId,
    label: seed.label,
    authRef: seed.authRef,
    scope: ["internal"],
    priority: seed.priority,
    strategy: null,
    concurrency: seed.concurrency ?? 1,
    ratePerMin: seed.ratePerMin ?? null,
    status: seed.status ?? "active",
    plan: {
      name: seed.plan.name,
      price_usd: seed.plan.price,
      billing_day: seed.plan.day,
      renews_on: seed.plan.price > 0 ? nextMonthlyReset(seed.plan.day, now) : undefined,
    },
    meta: seed.meta ?? {},
    health,
    sessionsCount: seed.sessions ?? 0,
    callsToday: seed.callsToday ?? 0,
    units: seed.units.map((unit) => buildUnit(unit, now)),
  };
}

const PROVIDERS: WorldProvider[] = [
  { id: "clay", name: "Clay", kind: "tool", executor: "mcp", defaultStrategy: "sticky", enabled: true },
  { id: "reoon", name: "Reoon", kind: "tool", executor: "api", defaultStrategy: "failover", enabled: true },
  { id: "zerobounce", name: "ZeroBounce", kind: "tool", executor: "api", defaultStrategy: "failover", enabled: true },
  { id: "apollo", name: "Apollo", kind: "tool", executor: "api", defaultStrategy: "most_remaining", enabled: true },
  { id: "hunter", name: "Hunter", kind: "tool", executor: "api", defaultStrategy: "failover", enabled: true },
  { id: "claude", name: "Claude", kind: "ai", executor: "cli_agent", defaultStrategy: "most_remaining", enabled: true },
  { id: "gemini", name: "Gemini", kind: "ai", executor: "cli_agent", defaultStrategy: "failover", enabled: true },
  { id: "codex", name: "Codex", kind: "ai", executor: "cli_agent", defaultStrategy: "failover", enabled: true },
  { id: "hermes", name: "Hermes", kind: "ai", executor: "agent", defaultStrategy: "failover", enabled: true },
  { id: "notion", name: "Notion", kind: "tool", executor: "mcp", defaultStrategy: "failover", enabled: true },
  { id: "github", name: "GitHub", kind: "tool", executor: "mcp", defaultStrategy: "round_robin", enabled: true },
  { id: "linear", name: "Linear", kind: "tool", executor: "mcp", defaultStrategy: "failover", enabled: true },
  { id: "local-tools", name: "Local Tools", kind: "tool", executor: "mcp", defaultStrategy: "failover", enabled: true },
  { id: "resend", name: "Resend", kind: "tool", executor: "api", defaultStrategy: "failover", enabled: true },
];

const CAPABILITIES: WorldCapability[] = [
  {
    name: "verify_email",
    kind: "tool",
    description: "Check that an address can receive mail (valid, invalid, risky, catch-all).",
    defaultStrategy: "failover",
  },
  {
    name: "find_email",
    kind: "tool",
    description: "Find a person's work email from a name and company domain.",
    defaultStrategy: "failover",
  },
  {
    name: "find_person",
    kind: "tool",
    description: "Look up a person by name, title or LinkedIn URL.",
    defaultStrategy: "failover",
  },
  {
    name: "enrich_company",
    kind: "tool",
    description: "Firmographics, size, tech stack and funding for a company domain.",
    defaultStrategy: "sticky",
  },
  {
    name: "ask_ai",
    kind: "ai",
    description: "Delegate a task to another AI (Claude, Codex, Gemini, Hermes) and return its answer.",
    defaultStrategy: "most_remaining",
  },
  {
    name: "agent_task",
    kind: "ai",
    description: "Run a multi-step agent task with the Farm tools as its only toolset.",
    defaultStrategy: "failover",
  },
];

const ROUTES: [string, string[]][] = [
  ["verify_email", ["reoon", "zerobounce"]],
  ["find_email", ["hunter", "apollo"]],
  ["find_person", ["apollo", "clay"]],
  ["enrich_company", ["apollo", "clay"]],
  ["ask_ai", ["claude", "codex", "gemini", "hermes"]],
  ["agent_task", ["hermes", "claude"]],
];

function clayUnits(creditsUsed: number, reserved: number, actionsUsed: number, anchor: number): UnitSeed[] {
  return [
    {
      unit: "credits",
      period: "month",
      limit: 2500,
      used: creditsUsed,
      reserved,
      anchor,
      chargedOn: "success",
      cost: 0.074,
      est: 2,
    },
    { unit: "actions", period: "month", limit: 15000, used: actionsUsed, anchor, chargedOn: "attempt", est: 4 },
  ];
}

function connectionSeeds(now: number): ConnSeed[] {
  const hunterAnchor = 17;
  return [
    // --- Clay: seven Launch workspaces, one token store each (sticky: a job stays on one workspace) ---
    {
      id: "clay-01",
      providerId: "clay",
      label: "Clay Launch 01",
      authRef: "token-store:clay-01",
      priority: 1,
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Launch", price: 185, day: 3 },
      units: clayUnits(1720, 40, 8_900, 3),
      success: 2418,
      failure: 9,
      p50: 1380,
      lastSuccessMinAgo: 2,
      callsToday: 64,
    },
    {
      id: "clay-02",
      providerId: "clay",
      label: "Clay Launch 02",
      authRef: "token-store:clay-02",
      priority: 2,
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Launch", price: 185, day: 7 },
      units: clayUnits(300, 0, 1_000, 7),
      success: 1304,
      failure: 4,
      p50: 1410,
      lastSuccessMinAgo: 6,
      callsToday: 41,
    },
    {
      id: "clay-03",
      providerId: "clay",
      label: "Clay Launch 03",
      authRef: "token-store:clay-03",
      priority: 3,
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Launch", price: 185, day: 11 },
      units: clayUnits(310, 0, 1_700, 11),
      success: 402,
      failure: 1,
      p50: 1295,
      lastSuccessMinAgo: 9,
      callsToday: 12,
    },
    {
      id: "clay-04",
      providerId: "clay",
      label: "Clay Launch 04",
      authRef: "token-store:clay-04",
      priority: 4,
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Launch", price: 185, day: 14 },
      units: clayUnits(2410, 60, 13_200, 14),
      success: 3066,
      failure: 12,
      p50: 1520,
      lastSuccessMinAgo: 1,
      callsToday: 88,
    },
    {
      id: "clay-05",
      providerId: "clay",
      label: "Clay Launch 05",
      authRef: "token-store:clay-05",
      priority: 5,
      status: "paused",
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Launch", price: 185, day: 18 },
      units: clayUnits(1100, 0, 6_400, 18),
      success: 1522,
      failure: 6,
      p50: 1440,
      lastSuccessMinAgo: 26 * 60,
    },
    {
      id: "clay-06",
      providerId: "clay",
      label: "Clay Launch 06",
      authRef: "token-store:clay-06",
      priority: 6,
      status: "exhausted",
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Launch", price: 185, day: 22 },
      units: clayUnits(2500, 0, 14_320, 22),
      success: 3398,
      failure: 11,
      p50: 1465,
      lastSuccessMinAgo: 5 * 60 + 14,
      health: {
        lastErrorKind: "limit_reached",
        lastError: "Monthly credit allowance used up (2,500 / 2,500).",
        lastErrorAt: iso(now - (5 * HOUR + 12 * MINUTE)),
      },
    },
    {
      id: "clay-07",
      providerId: "clay",
      label: "Clay Launch 07",
      authRef: "token-store:clay-07",
      priority: 7,
      status: "needs_login",
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Clay Pro", price: 185, day: 27 },
      units: clayUnits(0, 0, 0, 27),
      success: 0,
      failure: 3,
      p50: null,
      lastSuccessMinAgo: null,
      health: {
        consecutiveFailures: 3,
        lastErrorKind: "needs_login",
        lastError: "OAuth refresh token rejected (invalid_grant). Sign in to Clay again on the Farm PC.",
        lastErrorAt: iso(now - 38 * MINUTE),
      },
    },

    // --- Reoon ---
    {
      id: "reoon-01",
      providerId: "reoon",
      label: "Reoon Free",
      authRef: "env:REOON_KEY_01",
      priority: 1,
      concurrency: 4,
      ratePerMin: 120,
      plan: { name: "Free", price: 0, day: 1 },
      units: [{ unit: "credits", period: "day", limit: 600, used: 212, reserved: 4, chargedOn: "attempt" }],
      success: 5204,
      failure: 31,
      p50: 640,
      lastSuccessMinAgo: 1,
      callsToday: 212,
    },
    {
      id: "reoon-02",
      providerId: "reoon",
      label: "Reoon Power",
      authRef: "env:REOON_KEY_02",
      priority: 2,
      concurrency: 8,
      ratePerMin: 240,
      plan: { name: "Power", price: 9.9, day: 21 },
      units: [
        { unit: "credits", period: "month", limit: 10000, used: 3140, anchor: 21, chargedOn: "attempt", cost: 0.00099 },
      ],
      success: 3098,
      failure: 17,
      p50: 580,
      lastSuccessMinAgo: 14,
    },

    // --- ZeroBounce (one account with an open circuit) ---
    {
      id: "zerobounce-01",
      providerId: "zerobounce",
      label: "ZeroBounce Free",
      authRef: "env:ZEROBOUNCE_KEY_01",
      priority: 1,
      concurrency: 2,
      ratePerMin: 60,
      plan: { name: "Free", price: 0, day: 1 },
      units: [{ unit: "credits", period: "month", limit: 100, used: 64, anchor: 1, chargedOn: "attempt" }],
      success: 612,
      failure: 24,
      p50: 910,
      lastSuccessMinAgo: 47,
      health: {
        circuit: "open",
        consecutiveFailures: 5,
        lastErrorKind: "server",
        lastError: "HTTP 503 from api.zerobounce.net (5 consecutive failures).",
        lastErrorAt: iso(now - 9 * MINUTE),
      },
    },
    {
      id: "zerobounce-02",
      providerId: "zerobounce",
      label: "ZeroBounce Credits",
      authRef: "env:ZEROBOUNCE_KEY_02",
      priority: 2,
      concurrency: 4,
      ratePerMin: 100,
      plan: { name: "Pay as you go", price: 39, day: 12 },
      units: [{ unit: "credits", period: "total", limit: 5000, used: 1860, chargedOn: "attempt", cost: 0.0078 }],
      success: 1804,
      failure: 8,
      p50: 870,
      lastSuccessMinAgo: 3,
    },

    // --- Apollo (one account in cooldown after a 429) ---
    {
      id: "apollo-01",
      providerId: "apollo",
      label: "Apollo Basic",
      authRef: "env:APOLLO_KEY_01",
      priority: 1,
      concurrency: 3,
      ratePerMin: 50,
      plan: { name: "Basic", price: 49, day: 9 },
      units: [
        {
          unit: "credits",
          period: "month",
          limit: 5000,
          used: 3970,
          reserved: 12,
          anchor: 9,
          chargedOn: "found",
          cost: 0.0098,
        },
      ],
      success: 4021,
      failure: 38,
      p50: 1120,
      lastSuccessMinAgo: 4,
      callsToday: 120,
    },
    {
      id: "apollo-02",
      providerId: "apollo",
      label: "Apollo Free",
      authRef: "env:APOLLO_KEY_02",
      priority: 2,
      concurrency: 1,
      ratePerMin: 10,
      plan: { name: "Free", price: 0, day: 25 },
      units: [{ unit: "credits", period: "month", limit: 120, used: 118, anchor: 25, chargedOn: "found" }],
      success: 301,
      failure: 44,
      p50: 1340,
      lastSuccessMinAgo: 13,
      health: {
        consecutiveFailures: 2,
        lastErrorKind: "rate_limited",
        lastError: "HTTP 429 from api.apollo.io; retry after 11 minutes.",
        lastErrorAt: iso(now - 2 * MINUTE),
        cooldownUntil: iso(now + 11 * MINUTE),
      },
    },

    // --- Hunter ---
    {
      id: "hunter-01",
      providerId: "hunter",
      label: "Hunter Starter",
      authRef: "env:HUNTER_KEY_01",
      priority: 1,
      concurrency: 2,
      ratePerMin: 15,
      plan: { name: "Starter", price: 34, day: hunterAnchor },
      units: [
        {
          unit: "searches",
          period: "month",
          limit: 500,
          used: 188,
          anchor: hunterAnchor,
          chargedOn: "found",
          cost: 0.068,
        },
        {
          unit: "verifications",
          period: "month",
          limit: 1000,
          used: 240,
          anchor: hunterAnchor,
          chargedOn: "attempt",
          cost: 0.034,
        },
      ],
      success: 622,
      failure: 15,
      p50: 980,
      lastSuccessMinAgo: 21,
    },
    {
      id: "hunter-02",
      providerId: "hunter",
      label: "Hunter Free",
      authRef: "env:HUNTER_KEY_02",
      priority: 2,
      concurrency: 1,
      ratePerMin: 10,
      plan: { name: "Free", price: 0, day: 1 },
      units: [
        { unit: "searches", period: "month", limit: 25, used: 9, anchor: 1, chargedOn: "found" },
        { unit: "verifications", period: "month", limit: 50, used: 12, anchor: 1, chargedOn: "attempt" },
      ],
      success: 40,
      failure: 2,
      p50: 1010,
      lastSuccessMinAgo: 3 * 60,
    },

    // --- AI pools: each account runs its own unmodified CLI in its own config dir ---
    {
      id: "claude-01",
      providerId: "claude",
      label: "Claude Max (primary)",
      authRef: "cli:claude-01",
      priority: 1,
      plan: { name: "Max 5x", price: 100, day: 5 },
      meta: {
        cli: "claude",
        email: "owner@farm.dev",
        max_parallel: 1,
        config_dir: "D:/farm-data/ai/claude-01",
        models: ["sonnet", "opus", "haiku"],
      },
      units: [
        {
          unit: "messages",
          period: "rolling_5h",
          limit: 225,
          used: 98,
          reserved: 2,
          resetInMs: 2 * HOUR + 14 * MINUTE,
        },
        { unit: "opus_messages", period: "week", limit: 150, used: 61 },
      ],
      success: 912,
      failure: 14,
      p50: 14_200,
      lastSuccessMinAgo: 3,
      sessions: 14,
      callsToday: 37,
    },
    {
      id: "claude-02",
      providerId: "claude",
      label: "Claude Max (standby)",
      authRef: "cli:claude-02",
      priority: 2,
      status: "needs_login",
      plan: { name: "Max 5x", price: 100, day: 12 },
      meta: {
        cli: "claude",
        email: "standby@farm.dev",
        max_parallel: 1,
        config_dir: "D:/farm-data/ai/claude-02",
        models: ["sonnet", "opus", "haiku"],
      },
      units: [{ unit: "messages", period: "rolling_5h", limit: 225, used: 0, resetInMs: 5 * HOUR }],
      success: 388,
      failure: 5,
      p50: 13_800,
      lastSuccessMinAgo: 3 * 60 + 22,
      sessions: 6,
      callsToday: 9,
      health: {
        consecutiveFailures: 1,
        lastErrorKind: "needs_login",
        lastError: "Claude CLI reports the session expired (401).",
        lastErrorAt: iso(now - 2 * HOUR - 15 * MINUTE),
      },
    },
    {
      id: "claude-03",
      providerId: "claude",
      label: "Claude Pro (overflow)",
      authRef: "cli:claude-03",
      priority: 3,
      status: "exhausted",
      plan: { name: "Pro", price: 20, day: 20 },
      meta: {
        cli: "claude",
        email: "overflow@farm.dev",
        max_parallel: 1,
        config_dir: "D:/farm-data/ai/claude-03",
        models: ["sonnet", "haiku"],
      },
      units: [{ unit: "messages", period: "rolling_5h", limit: 45, used: 45, resetInMs: 1 * HOUR + 52 * MINUTE }],
      success: 241,
      failure: 12,
      p50: 12_900,
      lastSuccessMinAgo: 70,
      sessions: 3,
      callsToday: 45,
      health: {
        lastErrorKind: "limit_reached",
        lastError: "Usage limit reached; the window resets at the time shown.",
        lastErrorAt: iso(now - 68 * MINUTE),
      },
    },
    {
      id: "gemini-01",
      providerId: "gemini",
      label: "Antigravity (Google AI Pro)",
      authRef: "cli:gemini-01",
      priority: 1,
      plan: { name: "AI Pro", price: 20, day: 3 },
      meta: {
        cli: "agy",
        email: "owner-gemini@farm.dev",
        max_parallel: 2,
        config_dir: "D:/farm-data/ai/gemini-01",
        models: ["gemini-3-pro", "gemini-3-flash"],
      },
      units: [{ unit: "requests", period: "day", limit: 1500, used: 212, reserved: 1 }],
      success: 744,
      failure: 21,
      p50: 9_600,
      lastSuccessMinAgo: 8,
      sessions: 9,
      callsToday: 22,
    },
    {
      id: "codex-01",
      providerId: "codex",
      label: "Codex (ChatGPT Plus)",
      authRef: "cli:codex-01",
      priority: 1,
      plan: { name: "Plus", price: 20, day: 8 },
      meta: {
        cli: "codex",
        email: "owner-codex@farm.dev",
        max_parallel: 1,
        config_dir: "D:/farm-data/ai/codex-01",
        models: ["gpt-5-codex", "gpt-5"],
      },
      units: [{ unit: "messages", period: "rolling_5h", limit: 40, used: 11, resetInMs: 3 * HOUR + 31 * MINUTE }],
      success: 203,
      failure: 7,
      p50: 18_400,
      lastSuccessMinAgo: 33,
      sessions: 5,
      callsToday: 11,
    },
    {
      id: "hermes-01",
      providerId: "hermes",
      label: "Hermes (farm-agent profile)",
      authRef: "cli:hermes-01",
      priority: 1,
      plan: { name: "Metered via Bifrost", price: 0, day: 1 },
      meta: {
        cli: "hermes",
        email: "owner-hermes@farm.dev",
        max_parallel: 1,
        config_dir: "D:/farm-data/ai/hermes-01",
        models: ["farm-agent"],
      },
      units: [{ unit: "budget_usd", period: "month", limit: 25, used: 6.42, anchor: 1, cost: 1, est: 0.05 }],
      success: 118,
      failure: 9,
      p50: 41_000,
      lastSuccessMinAgo: 52,
      sessions: 21,
      callsToday: 6,
    },
    // --- MCP Providers: Notion, GitHub, Linear, Local Tools ---
    {
      id: "notion-01",
      providerId: "notion",
      label: "Notion Workspace 01",
      authRef: "token-store:notion-01",
      priority: 1,
      plan: { name: "Plus", price: 12, day: 5 },
      meta: { auth_type: "oauth", namespace: "notion", expose: "discovery" },
      units: [{ unit: "requests", period: "month", limit: 50000, used: 12400, chargedOn: "attempt" }],
      success: 1240,
      failure: 4,
      p50: 420,
      lastSuccessMinAgo: 6,
      callsToday: 42,
    },
    {
      id: "notion-02",
      providerId: "notion",
      label: "Notion Workspace 02",
      authRef: "token-store:notion-02",
      priority: 2,
      plan: { name: "Plus", price: 12, day: 18 },
      meta: { auth_type: "oauth", namespace: "notion", expose: "discovery" },
      units: [{ unit: "requests", period: "month", limit: 50000, used: 4100, chargedOn: "attempt" }],
      success: 410,
      failure: 1,
      p50: 450,
      lastSuccessMinAgo: 32,
      callsToday: 11,
    },
    {
      id: "github-01",
      providerId: "github",
      label: "GitHub Machine User",
      authRef: "env:GITHUB_TOKEN",
      priority: 1,
      plan: { name: "Enterprise", price: 21, day: 1 },
      meta: { auth_type: "env", namespace: "github", expose: "direct" },
      units: [{ unit: "requests", period: "hour", limit: 5000, used: 340, chargedOn: "attempt" }],
      success: 3200,
      failure: 12,
      p50: 310,
      lastSuccessMinAgo: 2,
      callsToday: 95,
    },
    {
      id: "linear-01",
      providerId: "linear",
      label: "Linear Product Workspace",
      authRef: "token-store:linear-01",
      priority: 1,
      plan: { name: "Standard", price: 10, day: 14 },
      meta: { auth_type: "oauth", namespace: "linear", expose: "auto" },
      units: [{ unit: "requests", period: "month", limit: 100000, used: 18900, chargedOn: "attempt" }],
      success: 1890,
      failure: 5,
      p50: 390,
      lastSuccessMinAgo: 12,
      callsToday: 54,
    },
    {
      id: "local-tools-01",
      providerId: "local-tools",
      label: "Local CLI Stdio",
      authRef: "token-store:local-tools-01",
      priority: 1,
      plan: { name: "Local", price: 0, day: 1 },
      meta: { transport: "stdio", command: "node ./tools/mcp-server.js", namespace: "local", expose: "direct" },
      units: [],
      success: 540,
      failure: 0,
      p50: 45,
      lastSuccessMinAgo: 1,
      callsToday: 38,
    },
    // --- OpenAPI Provider: Resend ---
    {
      id: "resend-01",
      providerId: "resend",
      label: "Resend Marketing",
      authRef: "env:RESEND_API_KEY",
      priority: 1,
      plan: { name: "Pro", price: 20, day: 10 },
      meta: { openapi: "https://api.resend.com/openapi.json", auth_type: "env", namespace: "resend" },
      units: [{ unit: "emails", period: "month", limit: 50000, used: 8430, chargedOn: "attempt", cost: 0.0004 }],
      success: 8430,
      failure: 14,
      p50: 210,
      lastSuccessMinAgo: 18,
      callsToday: 110,
    },
  ];
}

// ---------------------------------------------------------------------------
// Runs: 40 recent requests, newest first, deterministic.
// ---------------------------------------------------------------------------
interface RunPlan {
  capability: string;
  callers: string[];
  // pools in the order the router tried them
  pools: string[];
}

const RUN_PLANS: RunPlan[] = [
  { capability: "verify_email", callers: ["claude", "claude", "cli", "hermes"], pools: ["reoon", "zerobounce"] },
  { capability: "verify_email", callers: ["claude", "claude", "test"], pools: ["reoon", "zerobounce"] },
  { capability: "find_email", callers: ["claude", "hermes"], pools: ["hunter", "apollo"] },
  { capability: "find_person", callers: ["claude", "hermes", "console"], pools: ["apollo", "clay"] },
  { capability: "enrich_company", callers: ["claude", "claude", "hermes"], pools: ["apollo", "clay"] },
  { capability: "ask_ai", callers: ["claude", "claude", "hermes"], pools: ["claude", "codex", "gemini"] },
  { capability: "agent_task", callers: ["claude", "console"], pools: ["hermes", "claude"] },
];

function pickConnection(
  connections: WorldConnection[],
  providerId: string,
  random: () => number,
): WorldConnection | null {
  const usable = connections.filter(
    (c) => c.providerId === providerId && c.status === "active" && c.health.circuit !== "open",
  );
  if (usable.length === 0) return null;
  return usable[Math.floor(random() * usable.length)] ?? null;
}

function buildRuns(connections: WorldConnection[], providers: WorldProvider[], now: number): RunRow[] {
  const random = mulberry32(20261004);
  const providerName = (id: string) => providers.find((p) => p.id === id)?.name ?? id;
  const runs: RunRow[] = [];
  let cursor = now - 40 * 1000;
  for (let i = 0; i < 40; i++) {
    const plan = RUN_PLANS[Math.floor(random() * RUN_PLANS.length)] as RunPlan;
    const caller = plan.callers[Math.floor(random() * plan.callers.length)] as string;
    const roll = random();
    const startedAt = cursor;
    cursor -= Math.round((2 + random() * 12) * MINUTE);
    const base = {
      id: `0b7e${String(1000 + i)}-5c1d-4f0a-9a52-${String(100000000000 + i * 7919)}`,
      capability: plan.capability,
      request_id: `5d2a${String(2000 + i)}-9c3b-41e8-8d6e-${String(200000000000 + i * 104729)}`,
      caller,
      started_at: iso(startedAt),
    };

    if (i === 0 && plan.pools[0]) {
      runs.push({
        ...base,
        strategy: "failover",
        status: "running",
        cost_usd: 0,
        cached: false,
        connection_id: null,
        connection_label: null,
        provider_id: null,
        provider_name: null,
        error_kind: null,
        error: null,
        finished_at: null,
        duration_ms: null,
        attempts_count: 0,
      });
      continue;
    }

    if (i === 1) {
      runs.push({
        id: "00000000-0000-0000-0000-000000000002",
        capability: "enrich_company",
        request_id: "5d2a2002-9c3b-41e8-8d6e-200000000002",
        caller: "claude",
        started_at: iso(startedAt),
        strategy: "failover",
        status: "succeeded",
        cost_usd: 0.12,
        cached: false,
        connection_id: "clay-02",
        connection_label: "Clay Launch 02",
        provider_id: "clay",
        provider_name: "Clay",
        error_kind: null,
        error: null,
        finished_at: iso(startedAt + 1200),
        duration_ms: 1200,
        attempts_count: 2,
      });
      continue;
    }

    if (roll < 0.1) {
      // cache hit
      runs.push({
        ...base,
        strategy: "failover",
        status: "succeeded",
        cost_usd: 0,
        cached: true,
        connection_id: null,
        connection_label: null,
        provider_id: null,
        provider_name: null,
        error_kind: null,
        error: null,
        finished_at: iso(startedAt + 22),
        duration_ms: 22,
        attempts_count: 0,
      });
      continue;
    }

    if (roll < 0.15) {
      runs.push({
        ...base,
        strategy: "failover",
        status: "blocked",
        cost_usd: 0,
        cached: false,
        connection_id: null,
        connection_label: null,
        provider_id: null,
        provider_name: null,
        error_kind: "budget_exhausted",
        error: `Monthly budget for ${providerName(plan.pools[0] ?? "")} would be exceeded; paid call refused.`,
        finished_at: iso(startedAt + 41),
        duration_ms: 41,
        attempts_count: 0,
      });
      continue;
    }

    const firstPool = plan.pools[0] ?? "";
    const firstConn = pickConnection(connections, firstPool, random);
    const failFirst = roll < 0.3;
    const failAll = roll >= 0.3 && roll < 0.38;
    const secondPool = plan.pools[1];
    const secondConn = secondPool ? pickConnection(connections, secondPool, random) : null;
    const isAi = plan.capability === "ask_ai" || plan.capability === "agent_task";
    const duration = Math.round(isAi ? 6_000 + random() * 38_000 : 500 + random() * 2_600);

    if (failAll || !firstConn || (failFirst && !secondConn)) {
      const kinds = [
        ["rate_limited", "HTTP 429; the pool is cooling down."],
        ["empty", "Provider returned no result for this input."],
        ["timeout", "No response within 30 s."],
      ] as const;
      const [kind, message] = kinds[Math.floor(random() * kinds.length)] ?? kinds[0];
      runs.push({
        ...base,
        strategy: "failover",
        status: "failed",
        cost_usd: 0,
        cached: false,
        connection_id: firstConn?.id ?? null,
        connection_label: firstConn?.label ?? null,
        provider_id: firstConn?.providerId ?? null,
        provider_name: firstConn ? providerName(firstConn.providerId) : null,
        error_kind: kind,
        error: message,
        finished_at: iso(startedAt + duration + 600),
        duration_ms: duration + 600,
        attempts_count: secondConn ? 2 : 1,
      });
      continue;
    }

    const finalConn = failFirst && secondConn ? secondConn : firstConn;
    const unitCost = finalConn.units[0]?.unitCostUsd ?? 0;
    const est = finalConn.providerId === "clay" ? 2 : 1;
    const metered = finalConn.providerId === "hermes" ? 0.04 + random() * 0.08 : 0;
    const cost = isAi ? metered : unitCost * est;
    runs.push({
      ...base,
      strategy: plan.capability === "enrich_company" ? "sticky" : "failover",
      status: "succeeded",
      cost_usd: Math.round(cost * 10_000) / 10_000,
      cached: false,
      connection_id: finalConn.id,
      connection_label: finalConn.label,
      provider_id: finalConn.providerId,
      provider_name: providerName(finalConn.providerId),
      error_kind: null,
      error: null,
      finished_at: iso(startedAt + duration),
      duration_ms: duration,
      attempts_count: failFirst ? 2 : 1,
    });
  }
  return runs;
}

// ---------------------------------------------------------------------------
// Build
// ---------------------------------------------------------------------------
export function buildWorld(now: number = Date.now()): World {
  const ownerEmail = "owner@farm.local";
  const connections = connectionSeeds(now).map((seed) => buildConnection(seed, now));
  const runs = buildRuns(connections, PROVIDERS, now);

  // Month-to-date spend. Plan charges accrue evenly (so the forecast stays readable on any day of the month);
  // variable costs are API-style usage on top of the plans.
  const startOfMonth = Date.UTC(new Date(now).getUTCFullYear(), new Date(now).getUTCMonth(), 1);
  const nextMonth = Date.UTC(new Date(now).getUTCFullYear(), new Date(now).getUTCMonth() + 1, 1);
  const fraction = Math.max((now - startOfMonth) / (nextMonth - startOfMonth), 1 / 31);
  const variableMonthly: Record<string, number> = { clay: 40, apollo: 12, zerobounce: 6, hermes: 24 };
  const spend: Record<string, WorldSpend | undefined> = {};
  for (const provider of PROVIDERS) {
    const plans = connections
      .filter((c) => c.providerId === provider.id)
      .reduce((sum, c) => sum + (c.plan.price_usd ?? 0), 0);
    spend[provider.id] = {
      usageUsd: Math.round((variableMonthly[provider.id] ?? 0) * fraction * 100) / 100,
      billingUsd: Math.round(plans * fraction * 100) / 100,
    };
  }

  const providerBudgets: Record<string, number | undefined> = {
    clay: 1300,
    reoon: 15,
    zerobounce: 50,
    apollo: 70,
    hunter: 40,
    claude: 240,
    gemini: 25,
    codex: 25,
    hermes: 40,
    notion: 50,
    github: 30,
    linear: 20,
    "local-tools": 0,
    resend: 30,
  };

  const world: World = {
    builtAt: now,
    ownerEmail,
    providers: PROVIDERS.map((p) => ({ ...p })),
    connections,
    capabilities: CAPABILITIES.map((c) => ({ ...c })),
    routes: ROUTES.flatMap(([capability, pools]) =>
      pools.map((providerId, index) => ({ capability, providerId, position: index + 1, enabled: true })),
    ),
    runs,
    alerts: [],
    commands: [],
    providerBudgets,
    providerBudgetHardStops: Object.fromEntries(Object.keys(providerBudgets).map((k) => [k, true])),
    globalBudgetUsd: 7000,
    globalBudgetHardStop: true,
    spend,
    entities: buildEntities(now),
    evidence: buildEvidence(now),
    facts: buildFacts(now),
    auditEvents: buildAuditEvents(now),
    mcpTools: buildMcpTools(now),
    aiJobs: buildAiJobs(now),
    aiConversations: buildAiConversations(now),
  };
  world.alerts = buildAlerts(world, now);
  world.commands = buildCommandHistory(ownerEmail, now);
  return world;
}

function buildEntities(_now: number): WorldEntity[] {
  return [
    { id: "ent-001", kind: "company", canonicalKey: "acme.corp", name: "Acme Corporation" },
    { id: "ent-002", kind: "company", canonicalKey: "stripe.com", name: "Stripe Inc." },
    { id: "ent-003", kind: "person", canonicalKey: "jane.smith@acme.corp", name: "Jane Smith" },
    { id: "ent-004", kind: "person", canonicalKey: "john.doe@stripe.com", name: "John Doe" },
  ];
}

function buildEvidence(now: number): WorldEvidence[] {
  return [
    {
      id: "ev-001",
      sha256: "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
      path: "/data/evidence/acme-landing.png",
      url: "https://acme.corp",
      thumbPath: "/data/evidence/acme-landing-thumb.png",
      capturedAt: iso(now - 2 * DAY),
      toolVersion: "crawl4ai-v0.4",
      robotsDecision: "allowed",
    },
    {
      id: "ev-002",
      sha256: "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
      path: "/data/evidence/stripe-pricing.png",
      url: "https://stripe.com/pricing",
      thumbPath: "/data/evidence/stripe-pricing-thumb.png",
      capturedAt: iso(now - 1 * DAY),
      toolVersion: "crawl4ai-v0.4",
      robotsDecision: "allowed",
    },
    {
      id: "ev-003",
      sha256: "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
      path: "/data/evidence/jane-linkedin.png",
      url: "https://linkedin.com/in/janesmith",
      thumbPath: "/data/evidence/jane-linkedin-thumb.png",
      capturedAt: iso(now - 5 * DAY),
      toolVersion: "agent-hermes",
      robotsDecision: "allowed",
    },
  ];
}

function buildFacts(now: number): WorldFact[] {
  return [
    {
      id: "fact-001",
      entityId: "ent-001",
      attribute: "tech_stack",
      value: { cms: "Next.js", hosting: "Vercel", cdn: "Cloudflare" },
      sourceConnectionId: "apollo-01",
      observedAt: iso(now - 2 * DAY),
      expiresAt: iso(now + 28 * DAY),
      confidence: 0.98,
      evidenceIds: ["ev-001"],
    },
    {
      id: "fact-002",
      entityId: "ent-001",
      attribute: "headcount",
      value: { count: 320, range: "250-500", engineering: 85 },
      sourceConnectionId: "clay-01",
      observedAt: iso(now - 35 * DAY),
      expiresAt: null,
      confidence: 0.85,
      evidenceIds: ["ev-001"],
    },
    {
      id: "fact-003",
      entityId: "ent-002",
      attribute: "pricing_model",
      value: { transaction_pct: 2.9, fixed_fee: 0.3, currency: "USD" },
      sourceConnectionId: "apollo-01",
      observedAt: iso(now - 1 * DAY),
      expiresAt: iso(now + 90 * DAY),
      confidence: 1.0,
      evidenceIds: ["ev-002"],
    },
    {
      id: "fact-004",
      entityId: "ent-003",
      attribute: "work_email",
      value: { email: "jane.smith@acme.corp", deliverability: "valid", mx_valid: true },
      sourceConnectionId: "reoon-01",
      observedAt: iso(now - 3 * DAY),
      expiresAt: iso(now + 60 * DAY),
      confidence: 0.99,
      evidenceIds: ["ev-003"],
    },
    {
      id: "fact-005",
      entityId: "ent-004",
      attribute: "former_role",
      value: { company: "PayCo Inc.", title: "Staff Engineer", until: "2024" },
      sourceConnectionId: "clay-02",
      observedAt: iso(now - 180 * DAY),
      expiresAt: iso(now - 10 * DAY),
      confidence: 0.7,
      evidenceIds: [],
    },
  ];
}

function buildAuditEvents(now: number): WorldAuditEvent[] {
  return [
    {
      id: 101,
      actor: "owner@farm.dev",
      action: "set_budget",
      target: "provider:clay",
      before: { monthly_usd: 1500, hard_stop: true },
      after: { monthly_usd: 2000, hard_stop: true },
      at: iso(now - 4 * HOUR),
    },
    {
      id: 102,
      actor: "owner@farm.dev",
      action: "set_route",
      target: "capability:verify_email",
      before: { order: ["reoon", "zerobounce"] },
      after: { order: ["zerobounce", "reoon"] },
      at: iso(now - 12 * HOUR),
    },
    {
      id: 103,
      actor: "system:account_manager",
      action: "pause_account",
      target: "connection:claude-03",
      before: { status: "active" },
      after: { status: "exhausted" },
      at: iso(now - 68 * MINUTE),
    },
    {
      id: 104,
      actor: "owner@farm.dev",
      action: "add_connection",
      target: "connection:notion-02",
      before: null,
      after: { id: "notion-02", provider: "notion", plan: "Plus" },
      at: iso(now - 1 * DAY),
    },
    {
      id: 105,
      actor: "owner@farm.dev",
      action: "update_strategy",
      target: "provider:github",
      before: { strategy: "failover" },
      after: { strategy: "round_robin" },
      at: iso(now - 2 * DAY),
    },
  ];
}

function buildMcpTools(now: number): WorldMcpTool[] {
  const syncedAt = iso(now - 30 * MINUTE);
  return [
    // Notion tools
    {
      id: "tool-notion-01",
      workspaceId: "default",
      provider: "notion",
      name: "search",
      description: "Search all pages and databases in the Notion workspace by title or content.",
      inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_notion_search",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
    {
      id: "tool-notion-02",
      workspaceId: "default",
      provider: "notion",
      name: "read_page",
      description: "Retrieve rich text and block contents of a Notion page.",
      inputSchema: { type: "object", properties: { page_id: { type: "string" } }, required: ["page_id"] },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_notion_read_page",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
    {
      id: "tool-notion-03",
      workspaceId: "default",
      provider: "notion",
      name: "create_page",
      description: "Create a new page in a database or under a parent page.",
      inputSchema: {
        type: "object",
        properties: { parent_id: { type: "string" }, title: { type: "string" }, content: { type: "string" } },
        required: ["parent_id", "title"],
      },
      outputSchema: null,
      annotations: { readOnly: false },
      schemaHash: "h_notion_create_page",
      syncedAt,
      enabled: true,
      readOnly: false,
    },
    {
      id: "tool-notion-04",
      workspaceId: "default",
      provider: "notion",
      name: "query_database",
      description: "Filter and query rows from a Notion database table.",
      inputSchema: {
        type: "object",
        properties: { database_id: { type: "string" }, filter: { type: "object" } },
        required: ["database_id"],
      },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_notion_query_db",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
    // GitHub tools
    {
      id: "tool-github-01",
      workspaceId: "default",
      provider: "github",
      name: "get_file_contents",
      description: "Read file contents from a repository branch or commit.",
      inputSchema: {
        type: "object",
        properties: { owner: { type: "string" }, repo: { type: "string" }, path: { type: "string" } },
        required: ["owner", "repo", "path"],
      },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_github_get_file",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
    {
      id: "tool-github-02",
      workspaceId: "default",
      provider: "github",
      name: "create_pull_request",
      description: "Open a pull request from a head branch to a base branch.",
      inputSchema: {
        type: "object",
        properties: {
          owner: { type: "string" },
          repo: { type: "string" },
          title: { type: "string" },
          head: { type: "string" },
          base: { type: "string" },
        },
        required: ["owner", "repo", "title", "head", "base"],
      },
      outputSchema: null,
      annotations: { readOnly: false },
      schemaHash: "h_github_create_pr",
      syncedAt,
      enabled: true,
      readOnly: false,
    },
    {
      id: "tool-github-03",
      workspaceId: "default",
      provider: "github",
      name: "search_code",
      description: "Search code across repositories with query terms.",
      inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_github_search_code",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
    {
      id: "tool-github-04",
      workspaceId: "default",
      provider: "github",
      name: "delete_branch",
      description: "Delete a remote branch in the repository.",
      inputSchema: {
        type: "object",
        properties: { owner: { type: "string" }, repo: { type: "string" }, branch: { type: "string" } },
        required: ["owner", "repo", "branch"],
      },
      outputSchema: null,
      annotations: { readOnly: false },
      schemaHash: "h_github_delete_branch",
      syncedAt,
      enabled: false,
      readOnly: false,
    },
    // Linear tools
    {
      id: "tool-linear-01",
      workspaceId: "default",
      provider: "linear",
      name: "list_issues",
      description: "Search and list issues matching team, state, or assignee.",
      inputSchema: { type: "object", properties: { team_id: { type: "string" }, query: { type: "string" } } },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_linear_list",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
    {
      id: "tool-linear-02",
      workspaceId: "default",
      provider: "linear",
      name: "create_issue",
      description: "Create a new issue in Linear under a designated team.",
      inputSchema: {
        type: "object",
        properties: { title: { type: "string" }, description: { type: "string" }, team_id: { type: "string" } },
        required: ["title", "team_id"],
      },
      outputSchema: null,
      annotations: { readOnly: false },
      schemaHash: "h_linear_create",
      syncedAt,
      enabled: true,
      readOnly: false,
    },
    // Local tools
    {
      id: "tool-local-01",
      workspaceId: "default",
      provider: "local-tools",
      name: "inspect_file",
      description: "Inspect local workspace file metadata, encoding, and syntax.",
      inputSchema: { type: "object", properties: { file_path: { type: "string" } }, required: ["file_path"] },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_local_inspect",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
    {
      id: "tool-local-02",
      workspaceId: "default",
      provider: "local-tools",
      name: "run_linter",
      description: "Run project linter and report diagnostic code issues.",
      inputSchema: { type: "object", properties: { target: { type: "string" } } },
      outputSchema: null,
      annotations: { readOnly: true },
      schemaHash: "h_local_lint",
      syncedAt,
      enabled: true,
      readOnly: true,
    },
  ];
}

function buildAiJobs(now: number): WorldAiJob[] {
  return [
    {
      id: "job-ai-001",
      task: "Analyze competitive pricing across 10 B2B SaaS platforms",
      ai: "claude",
      account: "claude-01",
      model: "sonnet",
      mode: "answer",
      cwd: null,
      conversationId: "conv-001",
      jsonSchema: null,
      timeoutS: 1800,
      state: "running",
      elapsedS: 42,
      tokens: 3410,
      costUsd: 0.045,
      nativeSessionId: "sess_claude_01_a83",
      error: null,
      createdAt: iso(now - 45 * 1000),
      updatedAt: iso(now - 3 * 1000),
    },
    {
      id: "job-ai-002",
      task: "Refactor database query performance for v_routes view",
      ai: "codex",
      account: "codex-01",
      model: "gpt-5-codex",
      mode: "edit",
      cwd: "D:/Harness Farm/wt-c3",
      conversationId: "conv-002",
      jsonSchema: null,
      timeoutS: 3600,
      state: "running",
      elapsedS: 118,
      tokens: 7850,
      costUsd: 0.12,
      nativeSessionId: "thread_codex_98f",
      error: null,
      createdAt: iso(now - 120 * 1000),
      updatedAt: iso(now - 2 * 1000),
    },
    {
      id: "job-ai-003",
      task: "Verify TypeScript schema sync contract across all endpoints",
      ai: "claude",
      account: "claude-01",
      model: "haiku",
      mode: "answer",
      cwd: null,
      conversationId: null,
      jsonSchema: null,
      timeoutS: 600,
      state: "succeeded",
      elapsedS: 18,
      tokens: 1940,
      costUsd: 0.008,
      nativeSessionId: "sess_claude_01_b11",
      error: null,
      createdAt: iso(now - 14 * MINUTE),
      updatedAt: iso(now - 13 * MINUTE),
    },
    {
      id: "job-ai-004",
      task: "Generate OpenAPI client adapter for Resend marketing API",
      ai: "gemini",
      account: "gemini-01",
      model: "gemini-3-pro",
      mode: "edit",
      cwd: "D:/Harness Farm/wt-c3",
      conversationId: "conv-003",
      jsonSchema: null,
      timeoutS: 1200,
      state: "succeeded",
      elapsedS: 34,
      tokens: 4200,
      costUsd: 0.021,
      nativeSessionId: "conv_agy_77x",
      error: null,
      createdAt: iso(now - 32 * MINUTE),
      updatedAt: iso(now - 31 * MINUTE),
    },
    {
      id: "job-ai-005",
      task: "Hermes autonomous market intelligence sweep for AI agents",
      ai: "hermes",
      account: "hermes-01",
      model: "farm-agent",
      mode: "answer",
      cwd: null,
      conversationId: "conv-004",
      jsonSchema: null,
      timeoutS: 3600,
      state: "cancelled",
      elapsedS: 90,
      tokens: 5100,
      costUsd: 0.05,
      nativeSessionId: "hermes_run_54",
      error: { kind: "cancelled", message: "User cancelled job via Console" },
      createdAt: iso(now - 2 * HOUR),
      updatedAt: iso(now - 2 * HOUR),
    },
  ];
}

function buildAiConversations(now: number): WorldAiConversation[] {
  return [
    {
      id: "conv-001",
      ai: "claude",
      account: "claude-01",
      nativeSessionId: "sess_claude_01_a83",
      turns: 5,
      tokens: 18450,
      costUsd: 0.28,
      lastJobId: "job-ai-001",
      createdAt: iso(now - 3 * HOUR),
      updatedAt: iso(now - 45 * 1000),
    },
    {
      id: "conv-002",
      ai: "codex",
      account: "codex-01",
      nativeSessionId: "thread_codex_98f",
      turns: 3,
      tokens: 12100,
      costUsd: 0.19,
      lastJobId: "job-ai-002",
      createdAt: iso(now - 1 * HOUR),
      updatedAt: iso(now - 120 * 1000),
    },
    {
      id: "conv-003",
      ai: "gemini",
      account: "gemini-01",
      nativeSessionId: "conv_agy_77x",
      turns: 2,
      tokens: 7300,
      costUsd: 0.035,
      lastJobId: "job-ai-004",
      createdAt: iso(now - 40 * MINUTE),
      updatedAt: iso(now - 31 * MINUTE),
    },
    {
      id: "conv-004",
      ai: "hermes",
      account: "hermes-01",
      nativeSessionId: "hermes_run_54",
      turns: 6,
      tokens: 24600,
      costUsd: 0.24,
      lastJobId: "job-ai-005",
      createdAt: iso(now - 5 * HOUR),
      updatedAt: iso(now - 2 * HOUR),
    },
  ];
}

function buildAlerts(world: World, now: number): AlertRow[] {
  const clay = world.spend.clay ?? { usageUsd: 0, billingUsd: 0 };
  const startOfMonth = Date.UTC(new Date(now).getUTCFullYear(), new Date(now).getUTCMonth(), 1);
  const nextMonth = Date.UTC(new Date(now).getUTCFullYear(), new Date(now).getUTCMonth() + 1, 1);
  const fraction = Math.max((now - startOfMonth) / (nextMonth - startOfMonth), 1 / 31);
  const clayForecast = Math.round(((clay.usageUsd + clay.billingUsd) / fraction) * 100) / 100;
  const dollars = (value: number) => `$${Math.round(value).toLocaleString("en-US")}`;
  return [
    {
      id: "a1c0f6de-0001-4c1e-9a10-000000000001",
      kind: "circuit_open",
      severity: "critical",
      message:
        "ZeroBounce Free (zerobounce-01) circuit opened after 5 consecutive 503 responses. verify_email falls back to ZeroBounce Credits.",
      ref: "zerobounce-01",
      created_at: iso(now - 9 * MINUTE),
      acked_at: null,
    },
    {
      id: "a1c0f6de-0002-4c1e-9a10-000000000002",
      kind: "needs_login",
      severity: "warn",
      message: "Claude Max (standby) session expired. Sign in again with: farm ai login claude-02",
      ref: "claude-02",
      created_at: iso(now - 2 * HOUR - 15 * MINUTE),
      acked_at: null,
    },
    {
      id: "a1c0f6de-0003-4c1e-9a10-000000000003",
      kind: "needs_login",
      severity: "warn",
      message: "Clay Launch 07 token store rejected the refresh token. Sign in to Clay again on the Farm PC.",
      ref: "clay-07",
      created_at: iso(now - 38 * MINUTE),
      acked_at: null,
    },
    {
      id: "a1c0f6de-0004-4c1e-9a10-000000000004",
      kind: "budget_forecast",
      severity: "warn",
      message: `Clay is forecast to reach ${dollars(clayForecast)} this month against a ${dollars(world.providerBudgets.clay ?? 0)} budget.`,
      ref: "clay",
      created_at: iso(now - 6 * HOUR),
      acked_at: null,
    },
    {
      id: "a1c0f6de-0005-4c1e-9a10-000000000005",
      kind: "quota_exhausted",
      severity: "info",
      message: "Clay Launch 06 used its full monthly allowance (2,500 credits). It resumes at the next reset.",
      ref: "clay-06",
      created_at: iso(now - 5 * HOUR - 12 * MINUTE),
      acked_at: null,
    },
    {
      id: "a1c0f6de-0006-4c1e-9a10-000000000006",
      kind: "quota_exhausted",
      severity: "info",
      message: "Claude Pro (overflow) hit its 5-hour usage limit. Retry window opens at the reset time.",
      ref: "claude-03",
      created_at: iso(now - 68 * MINUTE),
      acked_at: null,
    },
    {
      id: "a1c0f6de-0007-4c1e-9a10-000000000007",
      kind: "renewal_due",
      severity: "info",
      message: "Apollo Basic renews in 3 days at 79% usage. Keep the plan.",
      ref: "apollo-01",
      created_at: iso(now - 20 * HOUR),
      acked_at: iso(now - 19 * HOUR),
    },
  ];
}

function buildCommandHistory(owner: string, now: number): FarmCommand[] {
  const done = (
    id: string,
    kind: FarmCommand["kind"],
    payload: Record<string, unknown>,
    agoMs: number,
  ): FarmCommand => ({
    id,
    kind,
    payload,
    status: "done",
    result: { ok: true },
    created_by: owner,
    created_at: iso(now - agoMs),
    done_at: iso(now - agoMs + 1800),
  });
  return [
    done("c0de0000-0001-4000-8000-000000000001", "set_priority", { connection_id: "clay-04", priority: 4 }, 3 * HOUR),
    done("c0de0000-0002-4000-8000-000000000002", "pause", { connection_id: "clay-05" }, 26 * HOUR),
    done(
      "c0de0000-0003-4000-8000-000000000003",
      "set_strategy",
      { provider_id: "apollo", strategy: "most_remaining" },
      2 * DAY,
    ),
  ];
}
