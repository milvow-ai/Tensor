import { describe, expect, it } from "vitest";

import { generateEnvName, getLoginCardViewModel, type LoginSession } from "../login";

function makeSession(overrides: Partial<LoginSession>): LoginSession {
  return {
    id: "session-test-01",
    connection_id: "claude-02",
    provider_id: "claude",
    method: "paste_code",
    state: "queued",
    auth_url: null,
    user_code: null,
    verification_uri: null,
    needs_input: false,
    input_hint: null,
    expires_at: null,
    identity: {},
    evidence: {},
    error_kind: null,
    error_excerpt: null,
    created_by: "owner@farm.local",
    created_at: "2026-10-24T12:00:00.000Z",
    updated_at: "2026-10-24T12:00:00.000Z",
    finished_at: null,
    ...overrides,
  };
}

describe("Sign-in logic & view model (login.ts)", () => {
  describe("env-name generation", () => {
    it("generates REOON_KEY_01 after REOON_KEY_00", () => {
      const result = generateEnvName("reoon", ["REOON_KEY_00"]);
      expect(result).toBe("REOON_KEY_01");
    });

    it("generates REOON_KEY_02 when REOON_KEY_00 and REOON_KEY_01 exist", () => {
      const result = generateEnvName("reoon", ["REOON_KEY_00", "REOON_KEY_01"]);
      expect(result).toBe("REOON_KEY_02");
    });

    it("starts at 00 when no existing keys", () => {
      const result = generateEnvName("reoon", []);
      expect(result).toBe("REOON_KEY_00");
    });

    it("fills the first free gap (REOON_KEY_01 when 00 and 02 exist)", () => {
      const result = generateEnvName("reoon", ["REOON_KEY_00", "REOON_KEY_02"]);
      expect(result).toBe("REOON_KEY_01");
    });

    it("handles env: prefix in existing names", () => {
      const result = generateEnvName("reoon", ["env:REOON_KEY_00"]);
      expect(result).toBe("REOON_KEY_01");
    });

    it("formats provider name with uppercase and underscores", () => {
      const result = generateEnvName("zero-bounce", ["ZERO_BOUNCE_KEY_00"]);
      expect(result).toBe("ZERO_BOUNCE_KEY_01");
    });
  });

  describe("state -> card view model mapping for every state", () => {
    it("maps queued state", () => {
      const session = makeSession({ state: "queued" });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Getting the sign-in ready…");
      expect(vm.statusChipStatus).toBe("running");
      expect(vm.statusChipLabel).toBe("Queued");
      expect(vm.canCancel).toBe(true);
      expect(vm.isTerminal).toBe(false);
      expect(vm.actions).toContain("cancel");
    });

    it("maps starting state", () => {
      const session = makeSession({ state: "starting" });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Getting the sign-in ready…");
      expect(vm.statusChipStatus).toBe("running");
      expect(vm.statusChipLabel).toBe("Starting");
      expect(vm.canCancel).toBe(true);
      expect(vm.isTerminal).toBe(false);
      expect(vm.actions).toContain("cancel");
    });

    it("maps waiting_for_user with device_code", () => {
      const session = makeSession({
        state: "waiting_for_user",
        method: "device_code",
        user_code: "ABCD-EFGH",
        verification_uri: "https://auth.openai.com/codex/device",
        auth_url: "https://auth.openai.com/codex/device",
        expires_at: "2026-10-24T12:15:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Sign in with device code");
      expect(vm.statusChipStatus).toBe("waiting-for-you");
      expect(vm.userCode).toBe("ABCD-EFGH");
      expect(vm.verificationUri).toBe("https://auth.openai.com/codex/device");
      expect(vm.description).toContain("private window");
      expect(vm.canCancel).toBe(true);
      expect(vm.isTerminal).toBe(false);
      expect(vm.actions).toEqual(["copy_code", "open_verification_uri", "cancel"]);
    });

    it("maps waiting_for_user with paste_code (needs_input)", () => {
      const session = makeSession({
        state: "waiting_for_user",
        method: "paste_code",
        auth_url: "https://claude.ai/oauth/authorize",
        needs_input: true,
        input_hint: "Paste the code the page shows you",
        expires_at: "2026-10-24T12:15:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Sign in with provider");
      expect(vm.statusChipStatus).toBe("waiting-for-you");
      expect(vm.authUrl).toBe("https://claude.ai/oauth/authorize");
      expect(vm.needsInput).toBe(true);
      expect(vm.inputHint).toBe("Paste the code the page shows you");
      expect(vm.canCancel).toBe(true);
      expect(vm.isTerminal).toBe(false);
      expect(vm.actions).toEqual(["open_auth_url", "submit_input", "cancel"]);
    });

    it("maps verifying state", () => {
      const session = makeSession({ state: "verifying" });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Verifying account");
      expect(vm.statusChipStatus).toBe("running");
      expect(vm.statusChipLabel).toBe("Verifying");
      expect(vm.checklist).toEqual(["Signed in", "Checking who it is", "Test reply"]);
      expect(vm.canCancel).toBe(true);
      expect(vm.isTerminal).toBe(false);
      expect(vm.actions).toContain("cancel");
    });

    it("maps done state", () => {
      const session = makeSession({
        state: "done",
        identity: { email_masked: "us**@example.com", plan: "Pro" },
        evidence: { test_run_id: "run-001", models: ["sonnet", "opus"] },
        finished_at: "2026-10-24T12:05:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Ready");
      expect(vm.statusChipStatus).toBe("ready");
      expect(vm.statusChipLabel).toBe("Ready");
      expect(vm.identity?.email_masked).toBe("us**@example.com");
      expect(vm.identity?.plan).toBe("Pro");
      expect(vm.evidence?.models).toEqual(["sonnet", "opus"]);
      expect(vm.canCancel).toBe(false);
      expect(vm.isTerminal).toBe(true);
    });

    it("maps duplicate state", () => {
      const session = makeSession({
        state: "duplicate",
        error_excerpt: "same account as claude-01",
        finished_at: "2026-10-24T12:05:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Duplicate account");
      expect(vm.statusChipStatus).toBe("failed");
      expect(vm.statusChipLabel).toBe("Duplicate");
      expect(vm.errorExcerpt).toBe("same account as claude-01");
      expect(vm.canCancel).toBe(false);
      expect(vm.isTerminal).toBe(true);
      expect(vm.actions).toEqual(["start_again", "remove_slot"]);
    });

    it("maps needs_verification state", () => {
      const session = makeSession({
        state: "needs_verification",
        auth_url: "https://accounts.google.com/verify",
        error_excerpt: "The provider asks you to verify this account",
        finished_at: "2026-10-24T12:05:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Verification required");
      expect(vm.description).toContain("verify this account");
      expect(vm.statusChipStatus).toBe("waiting-for-you");
      expect(vm.statusChipLabel).toBe("Verify");
      expect(vm.authUrl).toBe("https://accounts.google.com/verify");
      expect(vm.canCancel).toBe(false);
      expect(vm.isTerminal).toBe(true);
      expect(vm.actions).toEqual(["open_auth_url", "check_again"]);
    });

    it("maps failed state", () => {
      const session = makeSession({
        state: "failed",
        error_excerpt: "OAuth authorization failed with code 400",
        finished_at: "2026-10-24T12:05:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Sign-in failed");
      expect(vm.statusChipStatus).toBe("failed");
      expect(vm.statusChipLabel).toBe("Failed");
      expect(vm.errorExcerpt).toBe("OAuth authorization failed with code 400");
      expect(vm.canCancel).toBe(false);
      expect(vm.isTerminal).toBe(true);
      expect(vm.actions).toEqual(["start_again"]);
    });

    it("maps expired state", () => {
      const session = makeSession({
        state: "expired",
        error_excerpt: "The sign-in window expired.",
        finished_at: "2026-10-24T12:05:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Sign-in expired");
      expect(vm.statusChipStatus).toBe("failed");
      expect(vm.statusChipLabel).toBe("Expired");
      expect(vm.errorExcerpt).toBe("The sign-in window expired.");
      expect(vm.canCancel).toBe(false);
      expect(vm.isTerminal).toBe(true);
      expect(vm.actions).toEqual(["start_again"]);
    });

    it("maps cancelled state", () => {
      const session = makeSession({
        state: "cancelled",
        error_excerpt: "Sign-in was cancelled.",
        finished_at: "2026-10-24T12:05:00.000Z",
      });
      const vm = getLoginCardViewModel(session);

      expect(vm.title).toBe("Sign-in cancelled");
      expect(vm.statusChipStatus).toBe("failed");
      expect(vm.statusChipLabel).toBe("Cancelled");
      expect(vm.errorExcerpt).toBe("Sign-in was cancelled.");
      expect(vm.canCancel).toBe(false);
      expect(vm.isTerminal).toBe(true);
      expect(vm.actions).toEqual(["start_again"]);
    });
  });
});
