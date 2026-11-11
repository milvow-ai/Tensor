/** Preview simple Farm glob rules. Advanced character classes remain explicitly unknown in the UI. */
export function mcpToolAllowed(value: unknown, name: string): boolean | null {
  if (value === undefined) return true;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const rules = value as { allow?: unknown; deny?: unknown };
  const allow = rules.allow ?? ["*"];
  const deny = rules.deny ?? [];
  if (!Array.isArray(allow) || !Array.isArray(deny)) return null;
  const patterns: unknown[] = [...allow, ...deny];
  if (patterns.some((p) => typeof p !== "string" || p.length > 200 || p.includes("["))) return null;
  const match = (p: string) => {
    const text = Array.from(name);
    let previous = new Array<boolean>(text.length + 1).fill(false);
    previous[0] = true;
    for (const token of p) {
      const next = new Array<boolean>(text.length + 1).fill(false);
      next[0] = token === "*" && previous[0];
      for (let i = 1; i <= text.length; i++) {
        next[i] =
          token === "*" ? previous[i] || next[i - 1] : previous[i - 1] && (token === "?" || token === text[i - 1]);
      }
      previous = next;
    }
    return previous[text.length];
  };
  return (allow as string[]).some(match) && !(deny as string[]).some(match);
}
