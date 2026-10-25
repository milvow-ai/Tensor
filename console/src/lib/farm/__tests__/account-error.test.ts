import { describe, expect, it } from "vitest";

import { describeAccount, formatResetIn, MAX_SUMMARY_CHARS, usableShareTone } from "../account-error";

const NOW = Date.parse("2026-10-25T12:00:00Z");
const inHours = (h: number) => new Date(NOW + h * 3_600_000).toISOString();

const GOOGLE_JSON = `{
  "error": {
    "code": 403,
    "message": "Caller is not eligible for Antigravity. See https://developers.google.com/some/very/long/path?with=query&and=more",
    "status": "PERMISSION_DENIED"
  }
}`;

describe("describeAccount", () => {
  it("shows nothing for a usable account, even with an old error", () => {
    const t = describeAccount({ status: "active", lastError: GOOGLE_JSON, lastErrorKind: "unknown" }, NOW);
    expect(t).toMatchObject({ usable: true, needsSignIn: false, summary: null });
  });

  it("treats an auth error on an active account as needs-sign-in and unusable", () => {
    const t = describeAccount(
      { status: "active", lastErrorKind: "unknown", lastError: "OAuth error: invalid_grant (token expired)" },
      NOW,
    );
    expect(t.usable).toBe(false);
    expect(t.needsSignIn).toBe(true);
    expect(t.summary).toBe("Sign-in expired — Sign in again");
  });

  it("uses the error kind when the text is unrecognisable", () => {
    const t = describeAccount({ status: "active", lastErrorKind: "auth", lastError: "boom" }, NOW);
    expect(t.needsSignIn).toBe(true);
  });

  it("ignores an auth error that is older than the last success", () => {
    const t = describeAccount(
      {
        status: "active",
        lastErrorKind: "auth",
        lastError: "token expired",
        lastErrorAt: inHours(-5),
        lastSuccessAt: inHours(-1),
      },
      NOW,
    );
    expect(t).toMatchObject({ usable: true, needsSignIn: false, summary: null });
  });

  it("status needs_login is needs-sign-in even without error text", () => {
    const t = describeAccount({ status: "needs_login" }, NOW);
    expect(t).toMatchObject({ usable: false, needsSignIn: true, raw: null });
    expect(t.summary).toBe("Sign-in expired — Sign in again");
  });

  it("maps quota errors with the reset time", () => {
    const t = describeAccount(
      {
        status: "exhausted",
        effectiveState: "cooldown",
        cooldownUntil: inHours(8),
        lastErrorKind: "limit_reached",
        lastError: "RESOURCE_EXHAUSTED: quota exceeded",
      },
      NOW,
    );
    expect(t.usable).toBe(false);
    expect(t.summary).toBe("Quota reached — resets in 8h");
  });

  it("maps quota errors without a reset time", () => {
    const t = describeAccount({ status: "exhausted", lastError: "429 Too Many Requests" }, NOW);
    expect(t.summary).toBe("Quota reached");
  });

  it("maps the Antigravity eligibility error and keeps JSON and URLs out of the summary", () => {
    const t = describeAccount({ status: "paused", lastError: GOOGLE_JSON }, NOW);
    expect(t.usable).toBe(false);
    expect(t.summary).toBe("Not eligible for Antigravity — verify the Google account");
    expect(t.raw).toBe(GOOGLE_JSON);
  });

  it("maps endpoint failures when the circuit is open", () => {
    const t = describeAccount(
      {
        status: "active",
        effectiveState: "circuit_open",
        circuit: "open",
        lastErrorKind: "server",
        lastError: "503 Service Unavailable",
      },
      NOW,
    );
    expect(t.usable).toBe(false);
    expect(t.summary).toBe("Provider endpoint unavailable");
  });

  it("a running cooldown makes the account unusable", () => {
    expect(describeAccount({ status: "active", cooldownUntil: inHours(1) }, NOW).usable).toBe(false);
    expect(describeAccount({ status: "active", cooldownUntil: inHours(-1) }, NOW).usable).toBe(true);
  });

  it("falls back to one clipped line with no URL or JSON", () => {
    const long = `Something odd happened ${"x".repeat(300)} see https://example.com/a/b/c`;
    const t = describeAccount({ status: "paused", lastError: long }, NOW);
    expect(t.summary).not.toBeNull();
    expect((t.summary ?? "").length).toBeLessThanOrEqual(MAX_SUMMARY_CHARS);
    expect(t.summary).not.toMatch(/https?:/);

    const json = describeAccount({ status: "paused", lastError: '{"foo": "bar"}' }, NOW);
    expect(json.summary).toBe("Last call failed — open Details");
    const multi = describeAccount({ status: "paused", lastError: 'Plain first line\n{\n  "a": 1\n}' }, NOW);
    expect(multi.summary).toBe("Plain first line");
  });
});

describe("formatResetIn", () => {
  it("is coarse", () => {
    expect(formatResetIn(inHours(0.5), NOW)).toBe("30m");
    expect(formatResetIn(inHours(8), NOW)).toBe("8h");
    expect(formatResetIn(inHours(72), NOW)).toBe("3d");
  });
});

describe("usableShareTone", () => {
  it("is green at 100%, amber in between, red at 0% or with no accounts", () => {
    expect(usableShareTone(4, 4)).toBe("positive");
    expect(usableShareTone(3, 4)).toBe("warning");
    expect(usableShareTone(1, 4)).toBe("warning");
    expect(usableShareTone(0, 4)).toBe("negative");
    expect(usableShareTone(0, 0)).toBe("negative");
  });
});
