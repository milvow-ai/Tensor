import type {
  AlertSeverity,
  CommandStatus,
  Connection,
  EffectiveState,
  FarmOverview,
  PoolHealth,
  PoolOverviewRow,
  RunStatus,
  SpendMonthRow,
} from "./types";

// ---------------------------------------------------------------------------
// Tones: one palette for every status badge and dot in the Console.
// Light mode uses the 700 shade on a 10% tint (AA on white); dark mode the 400 shade.
// ---------------------------------------------------------------------------
export type Tone = "ok" | "info" | "warn" | "alert" | "bad" | "muted";

export const TONE: Record<Tone, { badge: string; dot: string; text: string; bar: string }> = {
  ok: {
    badge: "bg-emerald-500/10 text-emerald-700 ring-emerald-600/25 dark:text-emerald-400 dark:ring-emerald-400/25",
    dot: "bg-emerald-500",
    text: "text-emerald-700 dark:text-emerald-400",
    bar: "bg-emerald-500",
  },
  info: {
    badge: "bg-sky-500/10 text-sky-700 ring-sky-600/25 dark:text-sky-400 dark:ring-sky-400/25",
    dot: "bg-sky-500",
    text: "text-sky-700 dark:text-sky-400",
    bar: "bg-sky-500",
  },
  warn: {
    badge: "bg-amber-500/10 text-amber-800 ring-amber-600/30 dark:text-amber-400 dark:ring-amber-400/25",
    dot: "bg-amber-500",
    text: "text-amber-800 dark:text-amber-400",
    bar: "bg-amber-500",
  },
  alert: {
    badge: "bg-violet-500/10 text-violet-700 ring-violet-600/25 dark:text-violet-400 dark:ring-violet-400/25",
    dot: "bg-violet-500",
    text: "text-violet-700 dark:text-violet-400",
    bar: "bg-violet-500",
  },
  bad: {
    badge: "bg-red-500/10 text-red-700 ring-red-600/25 dark:text-red-400 dark:ring-red-400/25",
    dot: "bg-red-500",
    text: "text-red-700 dark:text-red-400",
    bar: "bg-red-500",
  },
  muted: {
    badge: "bg-zinc-500/10 text-zinc-700 ring-zinc-500/25 dark:text-zinc-300 dark:ring-zinc-400/25",
    dot: "bg-zinc-400 dark:bg-zinc-500",
    text: "text-muted-foreground",
    bar: "bg-zinc-400",
  },
};

export const STATE_META: Record<EffectiveState, { label: string; tone: Tone; hint: string }> = {
  active: { label: "Active", tone: "ok", hint: "Routable: the router can pick this account." },
  cooldown: { label: "Cooldown", tone: "info", hint: "Rate limited; the router skips it until the cooldown ends." },
  exhausted: { label: "Exhausted", tone: "warn", hint: "A quota is used up; it resumes at the next reset." },
  needs_login: { label: "Needs login", tone: "alert", hint: "The session expired; a human has to sign in again." },
  circuit_open: {
    label: "Circuit open",
    tone: "bad",
    hint: "Too many consecutive failures; the circuit breaker is open.",
  },
  paused: { label: "Paused", tone: "muted", hint: "Paused by the owner; it will not be used." },
  disabled: { label: "Disabled", tone: "muted", hint: "Disabled in the registry." },
};

/** Display order for legends and sorting: most urgent first. */
export const STATE_ORDER: EffectiveState[] = [
  "circuit_open",
  "needs_login",
  "exhausted",
  "cooldown",
  "active",
  "paused",
  "disabled",
];

export const HEALTH_META: Record<PoolHealth, { label: string; tone: Tone }> = {
  healthy: { label: "Healthy", tone: "ok" },
  degraded: { label: "Degraded", tone: "warn" },
  down: { label: "Down", tone: "bad" },
  disabled: { label: "Disabled", tone: "muted" },
};

export const SEVERITY_META: Record<AlertSeverity, { label: string; tone: Tone }> = {
  critical: { label: "Critical", tone: "bad" },
  warn: { label: "Warning", tone: "warn" },
  info: { label: "Info", tone: "info" },
};

export const RUN_STATUS_META: Record<RunStatus, { label: string; tone: Tone }> = {
  succeeded: { label: "Succeeded", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
  blocked: { label: "Blocked", tone: "warn" },
  running: { label: "Running", tone: "info" },
};

export const COMMAND_STATUS_META: Record<CommandStatus, { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "info" },
  running: { label: "Running", tone: "info" },
  done: { label: "Done", tone: "ok" },
  rejected: { label: "Rejected", tone: "warn" },
  failed: { label: "Failed", tone: "bad" },
};

// ---------------------------------------------------------------------------
// Small derivations over view rows (sums of already-aggregated rows, no raw aggregation).
// ---------------------------------------------------------------------------
export function poolHref(pool: Pick<PoolOverviewRow, "provider_id" | "kind">): string {
  return pool.kind === "ai" ? `/pools/ai/${pool.provider_id}` : `/pools/${pool.provider_id}`;
}

export function poolsIndexHref(kind: "tool" | "ai"): string {
  return kind === "ai" ? "/pools/ai" : "/pools/tools";
}

export function summarizeOverview(overview: FarmOverview) {
  const pools = overview.pools;
  const total: SpendMonthRow | undefined = overview.spend.find((row) => row.provider_id === "total");
  const open = overview.alerts.filter((alert) => alert.acked_at === null);
  return {
    poolsTotal: pools.length,
    poolsHealthy: pools.filter((pool) => pool.health === "healthy").length,
    poolsDegraded: pools.filter((pool) => pool.health === "degraded").length,
    poolsDown: pools.filter((pool) => pool.health === "down").length,
    accountsTotal: pools.reduce((sum, pool) => sum + pool.accounts_total, 0),
    accountsActive: pools.reduce((sum, pool) => sum + pool.accounts_active, 0),
    accountsUsable: pools.reduce((sum, pool) => sum + pool.accounts_usable, 0),
    spend: total?.spend_usd ?? 0,
    budget: total?.budget_usd ?? null,
    forecast: total?.forecast_usd ?? 0,
    openAlerts: open.length,
    criticalAlerts: open.filter((alert) => alert.severity === "critical").length,
    warnAlerts: open.filter((alert) => alert.severity === "warn").length,
  };
}

/** The per-unit limit that binds soonest: highest used share. Null when nothing is limited. */
export function tightestUnit(connection: Connection) {
  let best: { unit: string; share: number } | null = null;
  for (const unit of connection.units) {
    if (unit.limit === null || unit.limit <= 0) continue;
    const share = (unit.used + unit.reserved) / unit.limit;
    if (!best || share > best.share) best = { unit: unit.unit, share };
  }
  return best;
}

/** Short reason text for an account that is not routable, or null when it is. */
export function stateReason(connection: Connection): string | null {
  const { health } = connection;
  switch (connection.effectiveState) {
    case "circuit_open":
      return health.lastError ?? "Circuit breaker open after repeated failures.";
    case "cooldown":
      return health.lastError ?? "Cooling down after a rate limit.";
    case "needs_login":
      return health.lastError ?? "Session expired.";
    case "exhausted":
      return "Quota used up until the next reset.";
    default:
      return null;
  }
}
