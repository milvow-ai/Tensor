import { cache } from "react";

import { redirect } from "next/navigation";
import { connection } from "next/server";

import { resolveDataSource } from "@/lib/farm/mode";
import type { DataSourceKind } from "@/lib/farm/types";
import { createClient } from "@/lib/supabase/server";

export interface SessionUser {
  email: string;
  name: string;
  mode: DataSourceKind;
}

export type OwnerCheck =
  | { status: "owner"; user: SessionUser }
  | { status: "signed_out" }
  | { status: "not_owner"; email: string };

const FIXTURE_USER: SessionUser = { email: "owner@farm.local", name: "Farm Owner", mode: "fixtures" };

type ServerClient = Awaited<ReturnType<typeof createClient>>;

/** True when `email` equals farm_settings.owner_email. RLS hides the row from anyone else, which also fails closed. */
export async function matchesOwnerEmail(supabase: ServerClient, email: string | undefined): Promise<boolean> {
  if (!email) return false;
  const { data } = await supabase.from("farm_settings").select("owner_email").limit(1).maybeSingle();
  const owner = (data as { owner_email?: string | null } | null)?.owner_email;
  return Boolean(owner) && owner?.trim().toLowerCase() === email.trim().toLowerCase();
}

/** Who is looking at the Console. Fixtures mode skips auth; Supabase mode needs the owner's session. */
export const checkOwner = cache(async (): Promise<OwnerCheck> => {
  // Who is looking is a per-request fact: opt every caller out of build-time prerendering.
  await connection();
  if (resolveDataSource() === "fixtures") return { status: "owner", user: FIXTURE_USER };

  const supabase = await createClient();
  const { data, error } = await supabase.auth.getUser();
  const email = data.user?.email;
  if (error || !email) return { status: "signed_out" };
  if (!(await matchesOwnerEmail(supabase, email))) return { status: "not_owner", email };
  const name = (data.user.user_metadata as { full_name?: string } | undefined)?.full_name ?? "Farm Owner";
  return { status: "owner", user: { email, name, mode: "supabase" } };
});

/** For pages and layouts: redirect unless the owner is signed in. */
export async function requireOwner(): Promise<SessionUser> {
  const result = await checkOwner();
  if (result.status === "owner") return result.user;
  redirect(result.status === "signed_out" ? "/login" : "/unauthorized");
}

/** For server actions: throw instead of redirecting. */
export async function assertOwner(): Promise<SessionUser> {
  const result = await checkOwner();
  if (result.status !== "owner") throw new Error("Not signed in as the Farm owner.");
  return result.user;
}
