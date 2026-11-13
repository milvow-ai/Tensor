import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { commandContext, readCommand, submitCommand } from "../actions";
import { runQueuedCommand } from "../queued-command";
import type { FarmCommand } from "../types";
import { webcrypto } from "node:crypto";

vi.mock("../actions", () => ({ commandContext: vi.fn(), readCommand: vi.fn(), submitCommand: vi.fn() }));

const pending: FarmCommand = {
  id: "command-1",
  kind: "add_accounts",
  payload: {},
  status: "queued",
  result: null,
  created_by: "owner",
  created_at: "2026-11-12",
  done_at: null,
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("crypto", webcrypto);
  const data = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => data.set(key, value),
    removeItem: (key: string) => data.delete(key),
  });
  vi.mocked(commandContext).mockResolvedValue("tenant-1:user-1");
  vi.mocked(submitCommand).mockResolvedValue({ ok: true, data: pending });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("keeps queued setup pending after 15 seconds and resolves only the actual terminal state", async () => {
  let done = false;
  vi.mocked(readCommand).mockImplementation(async () => ({
    ok: true,
    data: { ...pending, status: done ? "done" : "queued" },
  }));
  let settled = false;
  const result = runQueuedCommand("enroll", "add_accounts", { provider_id: "claude" }).then((value) => {
    settled = true;
    return value;
  });
  await vi.waitFor(() => expect(submitCommand).toHaveBeenCalledOnce());
  await vi.advanceTimersByTimeAsync(16_500);
  expect(settled).toBe(false);
  done = true;
  await vi.advanceTimersByTimeAsync(1500);
  expect((await result).status).toBe("done");
});

it("resumes the stored command after a polling failure without adding another account", async () => {
  vi.mocked(readCommand).mockResolvedValueOnce({ ok: false, error: "temporary network failure" });
  const first = runQueuedCommand("enroll", "add_accounts", { provider_id: "claude", emails: ["one@example.test"] });
  const failed = expect(first).rejects.toThrow("still tracked");
  await vi.waitFor(() => expect(submitCommand).toHaveBeenCalledOnce());
  await vi.advanceTimersByTimeAsync(1500);
  await failed;
  vi.mocked(readCommand).mockResolvedValue({ ok: true, data: { ...pending, status: "done" } });
  const resumed = await runQueuedCommand("enroll", "add_accounts", {
    emails: ["one@example.test"],
    provider_id: "claude",
  });
  expect(resumed.id).toBe(pending.id);
  expect(submitCommand).toHaveBeenCalledOnce();
});

it("preserves the pending request when the dashboard stops waiting", async () => {
  vi.mocked(readCommand).mockResolvedValue({ ok: true, data: pending });
  const body = { provider_id: "claude", emails: ["one@example.test"] };
  const stillPending = expect(runQueuedCommand("enroll", "add_accounts", body)).rejects.toThrow("still processing");
  await vi.waitFor(() => expect(submitCommand).toHaveBeenCalledOnce());
  await vi.advanceTimersByTimeAsync(90_000);
  await stillPending;
  vi.mocked(readCommand).mockResolvedValue({ ok: true, data: { ...pending, status: "done" } });
  expect((await runQueuedCommand("enroll", "add_accounts", body)).status).toBe("done");
  expect(submitCommand).toHaveBeenCalledOnce();
});

it("does not resume a different user's or a different enrollment body's receipt", async () => {
  vi.mocked(readCommand).mockResolvedValue({ ok: false, error: "offline" });
  const failedAttempt = async (email: string) => {
    const before = vi.mocked(submitCommand).mock.calls.length;
    const result = runQueuedCommand("enroll", "add_accounts", { emails: [email] });
    const failed = expect(result).rejects.toThrow("still tracked");
    await vi.waitFor(() => expect(submitCommand).toHaveBeenCalledTimes(before + 1));
    await vi.advanceTimersByTimeAsync(1500);
    await failed;
  };
  await failedAttempt("one@example.test");
  vi.mocked(commandContext).mockResolvedValue("tenant-2:user-2");
  await failedAttempt("one@example.test");
  await failedAttempt("two@example.test");
  expect(submitCommand).toHaveBeenCalledTimes(3);
});
