import { z } from "zod";

import { AI_CLIS, CHARGED_ON, PERIODS } from "./command-meta";
import { type CommandKind, POOL_STRATEGIES } from "./types";

// Command contract between the Console and the Farm's command consumer (`farm_commands`).
// The Console only ever sends names, ids and numbers. Secrets stay on the PC: an account's `auth_ref` is an
// env-var NAME (`env:CLAY_KEY_08`) or a `cli:<id>` reference, never a key.

const ENV_NAME = /^[A-Z][A-Z0-9_]{1,63}$/;
const SLUG = /^[a-z0-9][a-z0-9-]{1,39}$/;

// Prefixes and shapes of well-known secret formats. Anything matching is refused with a specific message.
const SECRET_SHAPES: RegExp[] = [
  /^sk[-_]/i,
  /^pk[-_]/i,
  /^rk[-_]/i,
  /^(ghp|gho|ghs|ghu|github_pat)_/i,
  /^xox[abprs]-/i,
  /^AKIA[0-9A-Z]{8,}/,
  /^AIza[0-9A-Za-z_-]{10,}/,
  /^eyJ[0-9A-Za-z_-]{6,}/,
  /^bearer\s/i,
  /^[A-Za-z0-9+/_-]{32,}={0,2}$/,
];

/** True when the text looks like a key or token rather than an environment-variable name. */
export function looksLikeSecret(value: string): boolean {
  const text = value.trim();
  if (!text) return false;
  if (SECRET_SHAPES.some((shape) => shape.test(text))) return true;
  // A real env-var name is UPPER_SNAKE; mixed case with digits and no separators reads like a key.
  return text.length >= 20 && /[a-z]/.test(text) && /[A-Z]/.test(text) && /\d/.test(text);
}

export const SECRET_REFUSAL =
  "This looks like a secret, not a name. Enter only the environment-variable NAME. Keys never go through the Console.";

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

const unitSpec = z.object({
  limit: z.number().positive().nullable(),
  period: z.enum(PERIODS),
  anchor: z.number().int().min(1).max(31).nullable(),
  charged_on: z.enum(CHARGED_ON),
});

const authRef = z.string().refine((value) => {
  const colon = value.indexOf(":");
  if (colon < 0) return false;
  const scheme = value.slice(0, colon);
  const rest = value.slice(colon + 1);
  if (scheme === "env") return ENV_NAME.test(rest) && !looksLikeSecret(rest);
  return (scheme === "cli" || scheme === "token-store") && SLUG.test(rest);
}, "auth_ref must be env:NAME, cli:<id> or token-store:<id>");

export const connectionSpecSchema = z.object({
  id: slugSchema,
  label: z.string().trim().min(1).max(60),
  auth_ref: authRef,
  scope: z.array(z.string().min(1).max(60)).min(1),
  priority: z.number().int().min(1).max(10000),
  concurrency: z.number().int().min(1).max(64),
  plan: z.object({
    name: z.string().max(40).optional(),
    price_usd: z.number().min(0).max(100000).optional(),
    billing_day: z.number().int().min(1).max(31).optional(),
  }),
  meta: z.record(z.string(), z.unknown()),
  units: z.record(z.string(), unitSpec),
});
export type ConnectionSpecPayload = z.infer<typeof connectionSpecSchema>;

export const commandPayloadSchemas = {
  pause: z.object({ connection_id: connectionId }),
  resume: z.object({ connection_id: connectionId }),
  set_priority: z.object({ connection_id: connectionId, priority: z.number().int().min(1).max(10000) }),
  set_strategy: z.object({ provider_id: connectionId, strategy: z.enum(POOL_STRATEGIES) }),
  add_connection: z.object({ provider_id: connectionId, connection: connectionSpecSchema }),
  remove_connection: z.object({ connection_id: connectionId }),
  test_connection: z.object({ connection_id: connectionId }),
  ack_alert: z.object({ alert_id: z.string().min(1).max(64) }),
} satisfies Partial<Record<CommandKind, z.ZodType>>;

export type SupportedCommandKind = keyof typeof commandPayloadSchemas;

export function isSupportedCommand(kind: string): kind is SupportedCommandKind {
  return Object.hasOwn(commandPayloadSchemas, kind);
}
