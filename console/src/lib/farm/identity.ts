import type { ConnectionMeta } from "./types";

export function accountIdentity(meta: ConnectionMeta) {
  const raw = meta.identity;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const identity = raw as Record<string, unknown>;
  if (typeof identity.account_hash !== "string" || !identity.account_hash) return null;
  const text = (key: string) => (typeof identity[key] === "string" ? identity[key].slice(0, 254) : null);
  const email = text("email") ?? text("email_masked");
  return email ? { email, name: text("display_name"), verifiedAt: text("verified_at") } : null;
}
