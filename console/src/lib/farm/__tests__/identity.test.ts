import { describe, expect, it } from "vitest";

import { accountIdentity } from "../identity";

describe("worker identity", () => {
  it("uses provider identity and never treats labels or offered models as identity", () => {
    expect(accountIdentity({ label: "someone@example.com", models: ["model"] })).toBeNull();
    expect(accountIdentity({ identity: { email: "someone@example.com" } })).toBeNull();
    expect(
      accountIdentity({ identity: { account_hash: "hash", email: "actual@example.com", display_name: "Actual" } }),
    ).toMatchObject({ email: "actual@example.com", name: "Actual" });
  });
});
