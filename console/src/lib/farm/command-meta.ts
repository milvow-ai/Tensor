// Display text and option lists for Console commands. Plain data (no validation library), so client components can
// import it without pulling the zod schemas in `commands.ts` into the browser bundle.

import type { CommandKind, POOL_STRATEGIES } from "./types";

export const PERIODS = ["minute", "hour", "day", "week", "month", "rolling_5h", "total", "none"] as const;
export const CHARGED_ON = ["attempt", "success", "found"] as const;
export const AI_CLIS = ["claude", "codex", "agy", "hermes"] as const;
export type AiCli = (typeof AI_CLIS)[number];

/** Which provider pool an AI CLI belongs to, and sensible default models (editable in the dialog). */
export const AI_CLI_INFO: Record<AiCli, { providerId: string; label: string; models: string[] }> = {
  claude: { providerId: "claude", label: "Claude Code", models: ["sonnet", "opus", "haiku"] },
  codex: { providerId: "codex", label: "Codex CLI", models: ["gpt-5-codex", "gpt-5"] },
  agy: { providerId: "gemini", label: "Antigravity (agy)", models: ["gemini-3-pro", "gemini-3-flash"] },
  hermes: { providerId: "hermes", label: "Hermes", models: ["farm-agent"] },
};

export const PERIOD_LABELS: Record<(typeof PERIODS)[number], string> = {
  minute: "per minute",
  hour: "per hour",
  day: "per day",
  week: "per week",
  month: "per month",
  rolling_5h: "rolling 5 h",
  total: "total (no reset)",
  none: "no period",
};

export const STRATEGY_LABELS: Record<(typeof POOL_STRATEGIES)[number], { label: string; hint: string }> = {
  failover: { label: "Failover", hint: "One account until it is low, then the next." },
  most_remaining: { label: "Most remaining", hint: "Balance by remaining credits." },
  round_robin: { label: "Round robin", hint: "Rotate accounts per request." },
  parallel_split: { label: "Parallel split", hint: "Split a batch across all eligible accounts." },
  sticky: { label: "Sticky", hint: "Keep one job on one account." },
  fit_check: { label: "Fit check", hint: "Pick the account that can finish the whole job." },
};

export const COMMAND_LABELS: Record<CommandKind, string> = {
  pause: "Pause account",
  resume: "Resume account",
  set_priority: "Set priority",
  set_strategy: "Set strategy",
  set_budget: "Set budget",
  add_connection: "Add account",
  update_connection: "Update account",
  remove_connection: "Remove account",
  set_route: "Set route",
  test_connection: "Test connection",
  ack_alert: "Acknowledge alert",
};

/** True when a command concerns this pool: its provider, one of its accounts, or an account being added to it. */
export function commandConcernsPool(command: { payload: Record<string, unknown> }, poolId: string): boolean {
  const { provider_id: provider, connection_id: connectionId } = command.payload;
  if (provider === poolId) return true;
  if (typeof connectionId === "string" && connectionId.startsWith(`${poolId}-`)) return true;
  const connection = command.payload.connection;
  const newId = typeof connection === "object" && connection ? (connection as { id?: unknown }).id : null;
  return typeof newId === "string" && newId.startsWith(`${poolId}-`);
}

/** The id of the thing a command acts on, for display in feeds (a new account's own id, not its pool's). */
export function commandTarget(kind: CommandKind, payload: Record<string, unknown>): string {
  const created = typeof payload.connection === "object" && payload.connection ? payload.connection : null;
  const value =
    payload.connection_id ?? (created as { id?: unknown } | null)?.id ?? payload.alert_id ?? payload.provider_id;
  return typeof value === "string" ? value : kind;
}
