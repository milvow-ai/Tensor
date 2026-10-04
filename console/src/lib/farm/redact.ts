const SECRET_SHAPES = [
  /^sk-[A-Za-z0-9_-]{8,}/,
  /^ghp_[A-Za-z0-9]{16,}/,
  /^AKIA[0-9A-Z]{8,}/,
  /^AIza[0-9A-Za-z_-]{10,}/,
  /^eyJ[0-9A-Za-z_-]{6,}/,
  /^bearer\s/i,
  /^[A-Za-z0-9+/_-]{32,}={0,2}$/,
];

/** True when the text looks like a key, token, or secret rather than a normal string. */
export function looksLikeSecret(value: string): boolean {
  const text = value.trim();
  if (!text) return false;
  if (SECRET_SHAPES.some((shape) => shape.test(text))) return true;
  // A real env-var name is UPPER_SNAKE; mixed case with digits and no separators reads like a key.
  return text.length >= 20 && /[a-z]/.test(text) && /[A-Z]/.test(text) && /\d/.test(text);
}

const SUSPICIOUS_KEYS = /^(api_?key|secret|password|token|auth|bearer|credential|private_?key)$/i;

/**
 * Recursively redacts any values or strings in a JSON-compatible structure
 * that look like secret keys, tokens, or credentials.
 */
export function redactJson<T>(value: T): T {
  if (value === null || value === undefined) return value;

  if (typeof value === "string") {
    if (looksLikeSecret(value)) return "[REDACTED]" as unknown as T;
    // Query param style: key=..., api_key=..., token=...
    return value.replace(/((?:api_?key|key|token|secret|auth)=)([^&\s]+)/gi, "$1[REDACTED]") as unknown as T;
  }

  if (Array.isArray(value)) {
    return value.map((item) => redactJson(item)) as unknown as T;
  }

  if (typeof value === "object") {
    const output: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      if (SUSPICIOUS_KEYS.test(k) && typeof v === "string" && v.length > 0) {
        output[k] = "[REDACTED]";
      } else {
        output[k] = redactJson(v);
      }
    }
    return output as T;
  }

  return value;
}
