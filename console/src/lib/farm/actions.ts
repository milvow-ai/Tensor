"use server";

import { assertOwner } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

import { commandPayloadSchemas, isSupportedCommand } from "./commands";
import { getFarmData } from "./data";
import type { CommandKind, FarmCommand, JsonObject } from "./types";

export type ActionResult<T> = { ok: true; data: T } | { ok: false; error: string };

/** Namespace browser command receipts by the actual authenticated principal and tenant. */
export async function commandContext(): Promise<string> {
  const owner = await assertOwner();
  if (owner.mode === "fixtures") return "fixtures:owner";
  const client = await createClient();
  const [auth, settings] = await Promise.all([
    client.auth.getUser(),
    client.from("farm_settings").select("tenant_id").limit(1).maybeSingle(),
  ]);
  const tenant = (settings.data as { tenant_id?: string } | null)?.tenant_id;
  if (!auth.data.user?.id || !tenant) throw new Error("Could not identify this Farm session.");
  return `${tenant}:${auth.data.user.id}`;
}

/** Validates a Console command and writes it to the queue. The Farm executes it and reports back. */
export async function submitCommand(kind: CommandKind, payload: JsonObject): Promise<ActionResult<FarmCommand>> {
  try {
    await assertOwner();
    if (!isSupportedCommand(kind)) return { ok: false, error: `The Console cannot send "${kind}" commands yet.` };
    const parsed = commandPayloadSchemas[kind].safeParse(payload);
    if (!parsed.success) {
      return { ok: false, error: parsed.error.issues[0]?.message ?? "The command payload is not valid." };
    }
    const data = await getFarmData();
    const cleanPayload = JSON.parse(JSON.stringify(parsed.data)) as JsonObject;
    return { ok: true, data: await data.enqueueCommand(kind, cleanPayload) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Could not queue the command." };
  }
}

/** Current status of one command, polled by the client until it is done, rejected or failed. */
export async function readCommand(id: string): Promise<ActionResult<FarmCommand | null>> {
  try {
    await assertOwner();
    const data = await getFarmData();
    return { ok: true, data: await data.getCommand(id) };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "Could not read the command." };
  }
}

/** Fetches full run detail including event trajectory and result envelope. */
export async function fetchRunDetail(id: string) {
  try {
    await assertOwner();
    const data = await getFarmData();
    return { ok: true as const, data: await data.getRunDetail(id) };
  } catch (error) {
    return { ok: false as const, error: error instanceof Error ? error.message : "Could not fetch run detail." };
  }
}
