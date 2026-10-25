// What an account row may say about its last error, and whether the account is usable.
//
// One pure function decides both, so the row chip, the "N of M accounts active" caption and the meter always agree.
// The raw CLI text (multi-line JSON, long URLs) never goes inline: `summary` is one short line, `raw` goes behind a
// "Details" popover.

import type { CircuitState, ConnectionStatus, EffectiveState } from "./types";

export const MAX_SUMMARY_CHARS = 120;

export interface AccountHealthInput {
  status: ConnectionStatus;
  /** From `v_connection_status.effective_state`; when present it wins over `status` + circuit + cooldown. */
  effectiveState?: EffectiveState;
  circuit?: CircuitState;
  cooldownUntil?: string | null;
  lastErrorKind?: string | null;
  lastError?: string | null;
  lastErrorAt?: string | null;
  lastSuccessAt?: string | null;
}

export interface AccountTruth {
  /** The router can use this account right now. */
  usable: boolean;
  /** Last error is an expired/missing sign-in (even when `status` still says active). */
  needsSignIn: boolean;
  /** One short plain line, or null when the row should show no error (account usable, or nothing to say). */
  summary: string | null;
  /** The full original text for the Details popover; null when there is none. */
  raw: string | null;
}

export type ShareTone = "positive" | "warning" | "negative";

const AUTH_KINDS = new Set(["auth", "needs_login"]);
const QUOTA_KINDS = new Set(["limit_reached", "rate_limited", "limit"]);
const DOWN_KINDS = new Set(["server", "timeout", "crash"]);

const AUTH_TEXT =
  /invalid_grant|refresh[ _-]?token|(?:token|session|credentials?|oauth|login)\b[^.\n]{0,40}\bexpired|\bexpired\b[^.\n]{0,40}\b(?:token|session|credentials?|oauth|login)|sign[ -]?in again|log[ -]?in again|not logged in|unauthenticated|unauthorized|\b401\b|needs[_ ]login/i;
const NOT_ELIGIBLE_TEXT = /not[ _]eligible|ineligible/i;
const QUOTA_TEXT = /quota|resource[_ ]exhausted|rate[ _-]?limit|usage limit|limit reached|too many requests|\b429\b/i;
const DOWN_TEXT =
  /unavailable|\b50[234]\b|bad gateway|overloaded|econnrefused|econnreset|enotfound|etimedout|timed? ?out|server[_ ]error|internal error|fetch failed/i;

function isFuture(iso: string | null | undefined, now: number): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  return Number.isFinite(t) && t > now;
}

function isAfter(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const ta = Date.parse(a);
  const tb = Date.parse(b);
  return Number.isFinite(ta) && Number.isFinite(tb) && ta > tb;
}

/** "8h", "35m", "3d": coarse, for "resets in …". */
export function formatResetIn(iso: string, now: number): string {
  const minutes = Math.max(1, Math.round((Date.parse(iso) - now) / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
}

function clip(text: string): string {
  return text.length <= MAX_SUMMARY_CHARS ? text : `${text.slice(0, MAX_SUMMARY_CHARS - 1).trimEnd()}…`;
}

/** First readable sentence of unknown error text: no URLs, no JSON, one line, at most 120 characters. */
function plainFallback(raw: string): string {
  const trimmed = raw.trim();
  if (/^[[{]/.test(trimmed)) return "Last call failed — open Details";
  const firstLine = trimmed.split(/\r?\n/).find((line) => line.trim().length > 0) ?? "";
  const plain = firstLine
    .replace(/https?:\/\/\S+/gi, "")
    .replace(/\s+/g, " ")
    .replace(/[\s:,;-]+$/, "")
    .trim();
  if (!plain || /[{}]/.test(plain)) return "Last call failed — open Details";
  return clip(plain);
}

function errorKindOf(input: AccountHealthInput, raw: string): "auth" | "ineligible" | "quota" | "down" | "other" {
  const kind = (input.lastErrorKind ?? "").toLowerCase();
  if (AUTH_KINDS.has(kind) || AUTH_TEXT.test(raw)) return "auth";
  if (NOT_ELIGIBLE_TEXT.test(raw)) return "ineligible";
  if (QUOTA_KINDS.has(kind) || QUOTA_TEXT.test(raw)) return "quota";
  if (DOWN_KINDS.has(kind) || DOWN_TEXT.test(raw)) return "down";
  return "other";
}

/** Decide, from one account's health, whether it is usable and what (if anything) its row should say. */
export function describeAccount(input: AccountHealthInput, now: number = Date.now()): AccountTruth {
  const raw = input.lastError?.trim() ? input.lastError.trim() : null;
  // An error older than the last success is history, not the account's state.
  const stale = isAfter(input.lastSuccessAt, input.lastErrorAt);
  const kind = raw && !stale ? errorKindOf(input, raw) : null;
  const needsSignIn = kind === "auth" || input.status === "needs_login" || input.effectiveState === "needs_login";

  const cooling = input.effectiveState === "cooldown" || isFuture(input.cooldownUntil, now);
  const circuitOpen = input.effectiveState === "circuit_open" || input.circuit === "open";
  const usable = !needsSignIn && !cooling && !circuitOpen && (input.effectiveState ?? input.status) === "active";

  if (usable) return { usable: true, needsSignIn: false, summary: null, raw };

  let summary: string | null = null;
  if (needsSignIn) {
    summary = "Sign-in expired — Sign in again";
  } else if (kind === "ineligible") {
    summary = /antigravity|\bagy\b|google|gemini/i.test(raw ?? "")
      ? "Not eligible for Antigravity — verify the Google account"
      : "Not eligible — verify the account";
  } else if (kind === "quota" || input.status === "exhausted") {
    summary =
      input.cooldownUntil && isFuture(input.cooldownUntil, now)
        ? `Quota reached — resets in ${formatResetIn(input.cooldownUntil, now)}`
        : "Quota reached";
  } else if (kind === "down") {
    summary = "Provider endpoint unavailable";
  } else if (raw && !stale) {
    summary = plainFallback(raw);
  }
  return { usable: false, needsSignIn, summary, raw };
}

/** MeterBar `color` for each tone (the kit's own colour variables). */
export const SHARE_TONE_COLOR: Record<ShareTone, string> = {
  positive: "var(--kit-positive, #16a34a)",
  warning: "var(--kit-warning, #d97706)",
  negative: "var(--kit-negative, #dc2626)",
};

/** Green when every account is usable, amber when some are, red when none (or no accounts). */
export function usableShareTone(usable: number, total: number): ShareTone {
  if (total <= 0 || usable <= 0) return "negative";
  return usable >= total ? "positive" : "warning";
}
