"use server";

// Server-side reads for the sign-in cards. Kept apart from login.ts: a "use server" module may only export
// async functions, and login.ts holds pure helpers the browser uses too.

import { getFarmData } from "./data";
import type { ConnectPublicKeyJwk, LoginSession } from "./types";

export async function readLoginSession(sessionId: string): Promise<LoginSession | null> {
  const data = await getFarmData();
  return data.getLoginSession(sessionId);
}

export async function readActiveLoginSession(connectionId: string): Promise<LoginSession | null> {
  const data = await getFarmData();
  return data.getActiveLoginSession(connectionId);
}

export async function readConnectPublicKey(): Promise<ConnectPublicKeyJwk | null> {
  const data = await getFarmData();
  return data.getConnectPublicKey();
}

export async function readFirstNeedsLoginConnection(providerId: string): Promise<string | null> {
  const data = await getFarmData();
  const page = await data.listConnections(providerId, { pageSize: 50 });
  const found = page.items.find((c) => c.status === "needs_login");
  return found?.id ?? null;
}
