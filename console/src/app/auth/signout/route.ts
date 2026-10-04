import { NextResponse } from "next/server";

import { resolveDataSource } from "@/lib/farm/mode";
import { createClient } from "@/lib/supabase/server";

export async function POST() {
  if (resolveDataSource() === "supabase") {
    const supabase = await createClient();
    await supabase.auth.signOut();
  }
  // A relative Location keeps the redirect on whatever host the visitor used, behind any proxy.
  return new NextResponse(null, { status: 303, headers: { Location: "/login" } });
}
