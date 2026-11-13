import { commandContext, readCommand, submitCommand } from "./actions";
import type { CommandKind, FarmCommand, JsonObject } from "./types";

const POLL_MS = 1_500;
const WAIT_MS = 90_000;

/** A timeout or a browser reload does not mean the Farm rejected a command.
 * Keep its durable id and resume the same command when this action is retried.
 */
export async function runQueuedCommand(key: string, kind: CommandKind, payload: JsonObject): Promise<FarmCommand> {
  const context = await commandContext();
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") {
      return Object.fromEntries(
        Object.entries(value)
          .filter(([k]) => k !== "request_id")
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([k, v]) => [k, canonical(v)]),
      );
    }
    return value;
  };
  // A paste is freshly sealed on every attempt. Its stable operation key identifies the pending submission.
  const fingerprintBody = kind === "save_secret" || kind === "submit_login_input" ? {} : payload;
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(canonical(fingerprintBody))),
  );
  const fingerprint = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const storageKey = `farm-command:v2:${context}:${key}:${fingerprint}`;
  const previous = localStorage.getItem(storageKey);
  const receipt = previous ? (JSON.parse(previous) as { id?: string; request_id?: string }) : {};
  let command: FarmCommand | null = null;
  if (receipt.id) {
    const result = await readCommand(receipt.id);
    if (!result.ok) throw new Error(`${result.error} Command ${receipt.id} is still tracked; retry to resume.`);
    command = result.data;
    if (!command) throw new Error(`Command ${receipt.id} could not be found. Refresh the account before retrying.`);
  } else {
    if (kind === "add_accounts") {
      receipt.request_id ??= crypto.randomUUID();
      localStorage.setItem(storageKey, JSON.stringify(receipt));
    }
    const result = await submitCommand(
      kind,
      kind === "add_accounts" ? { ...payload, request_id: receipt.request_id } : payload,
    );
    if (!result.ok) throw new Error(result.error);
    command = result.data;
    localStorage.setItem(storageKey, JSON.stringify({ ...receipt, id: command.id }));
  }
  const deadline = Date.now() + WAIT_MS;
  while (command.status === "queued" || command.status === "running") {
    if (Date.now() >= deadline) {
      throw new Error("Farm is still processing this request. Retry to check the same request.");
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    const result = await readCommand(command.id);
    if (!result.ok) throw new Error(`${result.error} Command ${command.id} is still tracked; retry to resume.`);
    if (!result.data) throw new Error("Farm could not read this request yet. Retry to check the same request.");
    command = result.data;
  }
  if (kind === "add_accounts" && command.status === "failed" && receipt.request_id) {
    // Retry the failed setup under the original request id so already-created accounts are reused.
    localStorage.setItem(storageKey, JSON.stringify({ request_id: receipt.request_id }));
  } else {
    localStorage.removeItem(storageKey);
  }
  return command;
}

export function requireCommandDone(command: FarmCommand): void {
  if (command.status !== "done") {
    throw new Error(typeof command.result?.error === "string" ? command.result.error : `Command ${command.status}`);
  }
}
