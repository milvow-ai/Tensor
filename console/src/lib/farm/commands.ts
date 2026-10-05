import { z } from "zod";

import { AI_CLIS, CHARGED_ON, PERIODS } from "./command-meta";
import { type CommandKind, POOL_STRATEGIES, type PoolStrategy } from "./types";

// Command contract between the Console and the Farm's command consumer (`farm_commands`).
// The Console only ever sends names, ids and numbers. Secrets stay on the PC: an account's `auth_ref` is an
// env-var NAME (`env:CLAY_KEY_08`) or a `cli:<id>` reference, never a key.

const ENV_NAME = /^[A-Z][A-Z0-9_]{1,63}$/;
const SLUG = /^[a-z0-9][a-z0-9-]{1,39}$/;

import { looksLikeSecret, SECRET_REFUSAL } from "./redact";
export { looksLikeSecret, SECRET_REFUSAL };

export const envNameSchema = z
  .string()
  .trim()
  .min(1, "Enter the env-var name")
  .superRefine((value, ctx) => {
    if (looksLikeSecret(value)) {
      ctx.addIssue({ code: "custom", message: SECRET_REFUSAL });
    } else if (!ENV_NAME.test(value)) {
      ctx.addIssue({
        code: "custom",
        message: "Use UPPER_SNAKE_CASE letters, digits and underscores, e.g. CLAY_KEY_08.",
      });
    }
  });

export const slugSchema = z
  .string()
  .trim()
  .toLowerCase()
  .regex(SLUG, "2 to 40 characters: lowercase letters, digits and dashes, e.g. clay-08.");

const unitName = z
  .string()
  .trim()
  .min(1, "Name the unit")
  .max(32)
  .regex(/^[a-z][a-z0-9_]*$/, "Lowercase letters, digits and _ only, e.g. credits.");

// --- Add account form (the values the dialog edits) -------------------------------------------------------------

export const unitFormSchema = z.object({
  unit: unitName,
  limit: z.number().positive("Must be greater than 0.").nullable(),
  period: z.enum(PERIODS),
  anchor: z.number().int().min(1, "1 to 31").max(31, "1 to 31").nullable(),
  chargedOn: z.enum(CHARGED_ON),
});
export type UnitFormValues = z.infer<typeof unitFormSchema>;

export const addAccountFormSchema = z
  .object({
    id: slugSchema,
    label: z.string().trim().min(1, "Give the account a label").max(60),
    authEnv: envNameSchema,
    planName: z.string().trim().min(1, "Plan name, e.g. Launch").max(40),
    planPrice: z.number("Enter the monthly price, 0 for free").min(0, "Cannot be negative").max(100000),
    billingDay: z.number("Enter 1 to 31").int("Whole number").min(1, "1 to 31").max(31, "1 to 31"),
    priority: z.number("Enter a number").int("Whole number").min(1, "At least 1").max(10000),
    concurrency: z.number("Enter a number").int("Whole number").min(1, "At least 1").max(64),
    units: z.array(unitFormSchema).min(1, "Add at least one unit").max(6),
  })
  .superRefine((form, ctx) => {
    const seen = new Set<string>();
    form.units.forEach((unit, index) => {
      if (seen.has(unit.unit)) {
        ctx.addIssue({ code: "custom", path: ["units", index, "unit"], message: "Duplicate unit name." });
      }
      seen.add(unit.unit);
    });
  });
export type AddAccountFormValues = z.infer<typeof addAccountFormSchema>;

export const addAiAccountFormSchema = z.object({
  cli: z.enum(AI_CLIS),
  id: slugSchema,
  label: z.string().trim().min(1, "Give the account a label").max(60),
  models: z
    .string()
    .trim()
    .min(1, "List at least one model")
    .refine(
      (value) =>
        value
          .split(",")
          .map((model) => model.trim())
          .filter(Boolean)
          .every((model) => /^[a-z0-9][a-z0-9._:-]{0,63}$/i.test(model)),
      "Comma-separated model names, e.g. sonnet, opus.",
    ),
  planName: z.string().trim().max(40),
  planPrice: z.number("Enter 0 for free").min(0, "Cannot be negative").max(100000),
});
export type AddAiAccountFormValues = z.infer<typeof addAiAccountFormSchema>;

// --- Command payloads (what is written into farm_commands.payload) -----------------------------------------------

const connectionId = z.string().trim().min(1).max(60);

export const commandUnitSpecSchema = z.object({
  limit: z.number().min(0).nullable().default(null),
  period: z.enum(PERIODS).default("month"),
  anchor: z.number().int().min(1).max(31).nullable().default(null),
  charged_on: z.enum(CHARGED_ON).default("attempt"),
  unit_cost_usd: z.number().min(0).default(0),
  estimate_per_call: z.number().min(0).default(1),
});
export type CommandUnitSpecPayload = z.infer<typeof commandUnitSpecSchema>;

const authRef = z.string().refine((value) => {
  const colon = value.indexOf(":");
  if (colon < 0) return false;
  const scheme = value.slice(0, colon);
  const rest = value.slice(colon + 1);
  if (scheme === "env") return ENV_NAME.test(rest) && !looksLikeSecret(rest);
  return (scheme === "cli" || scheme === "token-store") && SLUG.test(rest);
}, "auth_ref must be env:NAME, cli:<id> or token-store:<id>");

export const planSpecSchema = z
  .object({
    name: z.string().max(40).optional(),
    price_usd: z.number().min(0).max(100000).optional(),
    billing_day: z.number().int().min(1).max(31).optional(),
  })
  .passthrough();

const baseAddConnectionSchema = z.object({
  provider_id: connectionId,
  id: slugSchema,
  auth_ref: authRef,
  label: z.string().trim().max(60).default(""),
  scope: z.array(z.string().min(1).max(60)).default(["internal"]),
  priority: z.number().int().min(0).default(100),
  strategy: z
    .enum([...POOL_STRATEGIES, "pin"] as const)
    .nullable()
    .optional(),
  concurrency: z.number().int().min(1).default(1),
  rate_per_min: z.number().int().min(1).nullable().optional(),
  status: z.enum(["active", "paused", "needs_login", "exhausted", "disabled"]).default("active"),
  plan: planSpecSchema.default({}),
  meta: z.record(z.string(), z.unknown()).default({}),
  units: z.record(z.string(), commandUnitSpecSchema).default({}),
});

// Flat and strict, exactly like the Farm's AddConnectionPayload (extra="forbid"); the contract test checks the two agree.
export const addConnectionPayloadSchema = baseAddConnectionSchema.strict();

export const connectionSpecSchema = baseAddConnectionSchema;
export type ConnectionSpecPayload = z.infer<typeof baseAddConnectionSchema>;

export const commandPayloadSchemas = {
  pause: z.object({
    connection_id: connectionId,
    reason: z.string().nullable().optional(),
  }),
  resume: z.object({
    connection_id: connectionId,
  }),
  set_priority: z.object({
    connection_id: connectionId,
    priority: z.number().int().min(0),
  }),
  set_strategy: z.object({
    strategy: z.enum([...POOL_STRATEGIES, "pin"] as const),
    provider_id: connectionId.nullable().optional(),
    connection_id: connectionId.nullable().optional(),
    capability: z.string().trim().min(1).nullable().optional(),
  }),
  set_budget: z.object({
    scope: z.enum(["global", "provider", "connection"]),
    monthly_usd: z.number().min(0),
    ref: z.string().nullable().optional(),
    hard_stop: z.boolean().default(true),
  }),
  add_connection: addConnectionPayloadSchema,
  update_connection: z.object({
    connection_id: connectionId,
    label: z.string().trim().min(1).max(60).nullable().optional(),
    auth_ref: authRef.nullable().optional(),
    scope: z.array(z.string().min(1).max(60)).nullable().optional(),
    priority: z.number().int().min(0).nullable().optional(),
    strategy: z
      .enum([...POOL_STRATEGIES, "pin"] as const)
      .nullable()
      .optional(),
    concurrency: z.number().int().min(1).nullable().optional(),
    rate_per_min: z.number().int().min(1).nullable().optional(),
    status: z.enum(["active", "paused", "needs_login", "exhausted", "disabled"]).nullable().optional(),
    plan: z.record(z.string(), z.unknown()).nullable().optional(),
    meta: z.record(z.string(), z.unknown()).nullable().optional(),
  }),
  remove_connection: z.object({
    connection_id: connectionId,
  }),
  set_route: z.object({
    capability: z.string().trim().min(1),
    provider_id: connectionId,
    position: z.number().int().default(0),
    enabled: z.boolean().default(true),
  }),
  test_connection: z.object({
    connection_id: connectionId,
    capability: z.string().trim().min(1).nullable().optional(),
  }),
  ack_alert: z.object({
    alert_id: z.string().min(1).max(64),
  }),
  cancel_ai_job: z.object({
    job_id: z.string().min(1).max(64),
  }),
  set_max_parallel: z.object({
    connection_id: connectionId.nullable().optional(),
    provider_id: connectionId.nullable().optional(),
    max_parallel: z.number().int().min(1).max(100),
  }),
  set_mcp_tool_access: z.object({
    provider_id: connectionId,
    tool: z.string().min(1).max(200),
    enabled: z.boolean().default(true),
    access: z.enum(["allow", "deny"]).optional(),
  }),
  sync_mcp_tools: z.object({
    provider_id: connectionId.nullable().optional(),
  }),
  add_provider: z
    .object({
      provider_id: slugSchema,
      name: z.string().optional().nullable(),
      kind: z.enum(["tool", "ai"]).default("tool"),
      executor: z
        .enum(["api", "mcp", "llm", "cli_agent", "agent", "browser", "local", "human"])
        .default("mcp"),
      default_strategy: z.enum([...POOL_STRATEGIES, "pin"] as const).default("failover"),
      enabled: z.boolean().default(true),
      config: z.record(z.string(), z.unknown()).default({}),
      mcp: z.record(z.string(), z.unknown()).optional().nullable(),
      command: z.string().optional().nullable(),
      args: z.array(z.string()).default([]),
      cwd: z.string().optional().nullable(),
      env: z.union([z.record(z.string(), z.string()), z.array(z.string())]).default({}),
      url: z.string().optional().nullable(),
      headers: z.record(z.string(), z.string()).default({}),
      auth: z.enum(["none", "env", "oauth"]).default("none"),
      namespace: slugSchema.optional().nullable(),
      exposure: z.enum(["direct", "discovery", "auto"]).default("auto"),
      timeout_s: z.number().positive().optional().nullable(),
      cli: z.enum(AI_CLIS).optional().nullable(),
      account_id: slugSchema.optional().nullable(),
      label: z.string().optional().nullable(),
      models: z.array(z.string()).default([]),
      max_parallel: z.number().int().min(1).default(1),
      spec: z.string().optional().nullable(),
      auth_env: z.string().optional().nullable(),
      connection_id: slugSchema.optional().nullable(),
      auth_ref: z.string().optional().nullable(),
      priority: z.number().int().min(0).default(100),
      concurrency: z.number().int().min(1).default(1),
      rate_per_min: z.number().int().min(1).optional().nullable(),
      status: z.enum(["active", "paused", "needs_login", "exhausted", "disabled"]).optional().nullable(),
      plan: z.record(z.string(), z.unknown()).default({}),
      meta: z.record(z.string(), z.unknown()).default({}),
      units: z.record(z.string(), commandUnitSpecSchema).default({}),
    })
    .passthrough(),
  update_provider: z.object({
    provider_id: slugSchema,
    name: z.string().optional().nullable(),
    enabled: z.boolean().optional().nullable(),
    default_strategy: z.enum([...POOL_STRATEGIES, "pin"] as const).optional().nullable(),
    config: z.record(z.string(), z.unknown()).optional().nullable(),
    mcp: z.record(z.string(), z.unknown()).optional().nullable(),
  }),
  remove_provider: z.object({
    provider_id: slugSchema,
    force: z.boolean().default(false),
  }),
} satisfies Record<CommandKind, z.ZodType>;

export type SupportedCommandKind = keyof typeof commandPayloadSchemas;

export function isSupportedCommand(kind: string): kind is SupportedCommandKind {
  return Object.hasOwn(commandPayloadSchemas, kind);
}

// --- Payload builders -------------------------------------------------------------------------------------------

export interface PauseArgs {
  connection_id: string;
  reason?: string | null;
}

export function buildPausePayload(args: PauseArgs) {
  return {
    connection_id: args.connection_id,
    ...(args.reason ? { reason: args.reason } : {}),
  };
}

export interface ResumeArgs {
  connection_id: string;
}

export function buildResumePayload(args: ResumeArgs) {
  return {
    connection_id: args.connection_id,
  };
}

export interface SetPriorityArgs {
  connection_id: string;
  priority: number;
}

export function buildSetPriorityPayload(args: SetPriorityArgs) {
  return {
    connection_id: args.connection_id,
    priority: args.priority,
  };
}

export interface SetStrategyArgs {
  strategy: PoolStrategy | "pin";
  provider_id?: string | null;
  connection_id?: string | null;
  capability?: string | null;
}

export function buildSetStrategyPayload(args: SetStrategyArgs) {
  return {
    strategy: args.strategy,
    ...(args.provider_id ? { provider_id: args.provider_id } : {}),
    ...(args.connection_id ? { connection_id: args.connection_id } : {}),
    ...(args.capability ? { capability: args.capability } : {}),
  };
}

export interface SetBudgetArgs {
  scope: "global" | "provider" | "connection";
  monthly_usd: number;
  ref?: string | null;
  hard_stop?: boolean;
}

export function buildSetBudgetPayload(args: SetBudgetArgs) {
  return {
    scope: args.scope,
    monthly_usd: args.monthly_usd,
    ...(args.ref !== undefined && args.ref !== null ? { ref: args.ref } : {}),
    ...(args.hard_stop !== undefined ? { hard_stop: args.hard_stop } : { hard_stop: true }),
  };
}

export interface AddConnectionUnitInput {
  limit?: number | null;
  period?: (typeof PERIODS)[number];
  anchor?: number | null;
  charged_on?: (typeof CHARGED_ON)[number];
  unit_cost_usd?: number;
  estimate_per_call?: number;
}

export interface AddConnectionArgs {
  provider_id: string;
  id: string;
  auth_ref: string;
  label?: string | null;
  scope?: string[];
  priority?: number;
  strategy?: PoolStrategy | "pin" | null;
  concurrency?: number;
  rate_per_min?: number | null;
  status?: "active" | "paused" | "needs_login" | "exhausted" | "disabled";
  plan?: Record<string, unknown>;
  meta?: Record<string, unknown>;
  units?: Record<string, AddConnectionUnitInput>;
}

export function buildAddConnectionPayload(args: AddConnectionArgs) {
  return {
    provider_id: args.provider_id,
    id: args.id,
    auth_ref: args.auth_ref,
    ...(args.label ? { label: args.label } : {}),
    scope: args.scope ?? ["internal"],
    priority: args.priority ?? 100,
    ...(args.strategy ? { strategy: args.strategy } : {}),
    concurrency: args.concurrency ?? 1,
    ...(args.rate_per_min !== undefined && args.rate_per_min !== null ? { rate_per_min: args.rate_per_min } : {}),
    status: args.status ?? "active",
    plan: args.plan ?? {},
    meta: args.meta ?? {},
    units: args.units ?? {},
  };
}

export interface UpdateConnectionArgs {
  connection_id: string;
  label?: string | null;
  auth_ref?: string | null;
  scope?: string[] | null;
  priority?: number | null;
  strategy?: PoolStrategy | "pin" | null;
  concurrency?: number | null;
  rate_per_min?: number | null;
  status?: "active" | "paused" | "needs_login" | "exhausted" | "disabled" | null;
  plan?: Record<string, unknown> | null;
  meta?: Record<string, unknown> | null;
}

export function buildUpdateConnectionPayload(args: UpdateConnectionArgs) {
  return {
    connection_id: args.connection_id,
    ...(args.label !== undefined && args.label !== null ? { label: args.label } : {}),
    ...(args.auth_ref !== undefined && args.auth_ref !== null ? { auth_ref: args.auth_ref } : {}),
    ...(args.scope !== undefined && args.scope !== null ? { scope: args.scope } : {}),
    ...(args.priority !== undefined && args.priority !== null ? { priority: args.priority } : {}),
    ...(args.strategy !== undefined && args.strategy !== null ? { strategy: args.strategy } : {}),
    ...(args.concurrency !== undefined && args.concurrency !== null ? { concurrency: args.concurrency } : {}),
    ...(args.rate_per_min !== undefined && args.rate_per_min !== null ? { rate_per_min: args.rate_per_min } : {}),
    ...(args.status !== undefined && args.status !== null ? { status: args.status } : {}),
    ...(args.plan !== undefined && args.plan !== null ? { plan: args.plan } : {}),
    ...(args.meta !== undefined && args.meta !== null ? { meta: args.meta } : {}),
  };
}

export interface RemoveConnectionArgs {
  connection_id: string;
}

export function buildRemoveConnectionPayload(args: RemoveConnectionArgs) {
  return {
    connection_id: args.connection_id,
  };
}

export interface SetRouteArgs {
  capability: string;
  provider_id: string;
  position?: number;
  enabled?: boolean;
}

export function buildSetRoutePayload(args: SetRouteArgs) {
  return {
    capability: args.capability,
    provider_id: args.provider_id,
    position: args.position ?? 0,
    enabled: args.enabled ?? true,
  };
}

export interface TestConnectionArgs {
  connection_id: string;
  capability?: string | null;
}

export function buildTestConnectionPayload(args: TestConnectionArgs) {
  return {
    connection_id: args.connection_id,
    ...(args.capability ? { capability: args.capability } : {}),
  };
}

export interface AckAlertArgs {
  alert_id: string;
}

export function buildAckAlertPayload(args: AckAlertArgs) {
  return {
    alert_id: args.alert_id,
  };
}

export interface CancelAiJobArgs {
  job_id: string;
}

export function buildCancelAiJobPayload(args: CancelAiJobArgs) {
  return {
    job_id: args.job_id,
  };
}

export interface SetMaxParallelArgs {
  connection_id?: string | null;
  provider_id?: string | null;
  max_parallel: number;
}

export function buildSetMaxParallelPayload(args: SetMaxParallelArgs) {
  return {
    ...(args.connection_id ? { connection_id: args.connection_id } : {}),
    ...(args.provider_id ? { provider_id: args.provider_id } : {}),
    max_parallel: args.max_parallel,
  };
}

export interface SetMcpToolAccessArgs {
  provider_id: string;
  tool: string;
  enabled?: boolean;
  access?: "allow" | "deny";
}

export function buildSetMcpToolAccessPayload(args: SetMcpToolAccessArgs) {
  return {
    provider_id: args.provider_id,
    tool: args.tool,
    enabled: args.enabled ?? (args.access === "allow" || args.access === undefined),
    ...(args.access ? { access: args.access } : {}),
  };
}

export interface SyncMcpToolsArgs {
  provider_id?: string | null;
}

export function buildSyncMcpToolsPayload(args: SyncMcpToolsArgs) {
  return {
    ...(args.provider_id ? { provider_id: args.provider_id } : {}),
  };
}

export const commandBuilders = {
  pause: buildPausePayload,
  resume: buildResumePayload,
  set_priority: buildSetPriorityPayload,
  set_strategy: buildSetStrategyPayload,
  set_budget: buildSetBudgetPayload,
  add_connection: buildAddConnectionPayload,
  update_connection: buildUpdateConnectionPayload,
  remove_connection: buildRemoveConnectionPayload,
  set_route: buildSetRoutePayload,
  test_connection: buildTestConnectionPayload,
  ack_alert: buildAckAlertPayload,
  cancel_ai_job: buildCancelAiJobPayload,
  set_max_parallel: buildSetMaxParallelPayload,
  set_mcp_tool_access: buildSetMcpToolAccessPayload,
  sync_mcp_tools: buildSyncMcpToolsPayload,
};

export function createContractExamplePayloads(): Record<CommandKind, Record<string, unknown>> {
  return {
    pause: buildPausePayload({
      connection_id: "c_contract",
      reason: "Routine maintenance",
    }),
    resume: buildResumePayload({
      connection_id: "c_paused",
    }),
    set_priority: buildSetPriorityPayload({
      connection_id: "c_contract",
      priority: 42,
    }),
    set_strategy: buildSetStrategyPayload({
      strategy: "most_remaining",
      provider_id: "p_contract",
    }),
    set_budget: buildSetBudgetPayload({
      scope: "provider",
      monthly_usd: 150,
      ref: "p_contract",
      hard_stop: true,
    }),
    add_connection: buildAddConnectionPayload({
      provider_id: "p_contract",
      id: "c_contract_added",
      auth_ref: "env:CONTRACT_KEY_NEW",
      label: "Contract Test Account",
      scope: ["internal"],
      priority: 75,
      concurrency: 2,
      status: "active",
      plan: { name: "Growth", price_usd: 49, billing_day: 15 },
      meta: { region: "us-east" },
      units: {
        credits: {
          limit: 5000,
          period: "month",
          anchor: 15,
          charged_on: "attempt",
          unit_cost_usd: 0.01,
          estimate_per_call: 1,
        },
      },
    }),
    update_connection: buildUpdateConnectionPayload({
      connection_id: "c_contract",
      label: "Updated Contract Account",
      priority: 25,
    }),
    remove_connection: buildRemoveConnectionPayload({
      connection_id: "c_to_remove",
    }),
    set_route: buildSetRoutePayload({
      capability: "cap_contract",
      provider_id: "p_contract",
      position: 1,
      enabled: true,
    }),
    test_connection: buildTestConnectionPayload({
      connection_id: "c_contract",
    }),
    ack_alert: buildAckAlertPayload({
      alert_id: "a0000000-0000-0000-0000-000000000001",
    }),
    cancel_ai_job: buildCancelAiJobPayload({
      job_id: "00000000-0000-0000-0000-000000000001",
    }),
    set_max_parallel: buildSetMaxParallelPayload({
      connection_id: "c_contract",
      max_parallel: 2,
    }),
    set_mcp_tool_access: buildSetMcpToolAccessPayload({
      provider_id: "p_contract",
      tool: "query_records",
      enabled: true,
    }),
    sync_mcp_tools: buildSyncMcpToolsPayload({
      provider_id: "p_contract",
    }),
    add_provider: {
      provider_id: "p_contract",
      name: "Contract Provider",
      kind: "tool",
      executor: "mcp",
      default_strategy: "failover",
      enabled: true,
      config: {},
      command: "python",
      args: ["-m", "fake_server"],
      auth: "none",
      exposure: "auto",
    },
    update_provider: {
      provider_id: "p_contract",
      name: "Contract Provider Updated",
      enabled: true,
      default_strategy: "round_robin",
    },
    remove_provider: {
      provider_id: "p_contract",
      force: true,
    },
  };
}
