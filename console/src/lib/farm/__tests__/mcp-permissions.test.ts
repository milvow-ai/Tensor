import { describe, expect, it } from "vitest";

import { mcpToolAllowed } from "../mcp-permissions";

describe("MCP permission preview", () => {
  it("applies deny rules after allow rules with case sensitive matching", () => {
    const rules = { allow: ["read_*", "Write"], deny: ["read_secret?"] };
    expect(mcpToolAllowed(rules, "read_name")).toBe(true);
    expect(mcpToolAllowed(rules, "read_secret1")).toBe(false);
    expect(mcpToolAllowed(rules, "write")).toBe(false);
  });
  it("does not interpret regex metacharacters as permission patterns", () => {
    expect(mcpToolAllowed({ allow: ["read.(x)"] }, "readXx")).toBe(false);
    expect(mcpToolAllowed({ allow: ["read.(x)"] }, "read.(x)")).toBe(true);
  });
  it("shows advanced or invalid rules as unknown rather than granting a permission", () => {
    expect(mcpToolAllowed({ allow: ["[ab]*"] }, "abc")).toBeNull();
    expect(mcpToolAllowed({ allow: "*" }, "abc")).toBeNull();
  });
});
