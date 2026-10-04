import { type NextRequest, NextResponse } from "next/server";

import type { EmailOtpType } from "@supabase/supabase-js";

import { matchesOwnerEmail } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

/** Only same-site relative paths are allowed as a post-login destination. */
function safeNext(value: string | null): string {
  if (!value?.startsWith("/") || value.startsWith("//")) return "/overview";
  return value;
}

/** Magic-link landing: exchange the code (or token hash) for a session, then check the owner email. */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl;
  const next = safeNext(searchParams.get("next"));
  const code = searchParams.get("code");
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;

  const supabase = await createClient();
  let failed = true;
  if (code) {
    failed = Boolean((await supabase.auth.exchangeCodeForSession(code)).error);
  } else if (tokenHash && type) {
    failed = Boolean((await supabase.auth.verifyOtp({ type, token_hash: tokenHash })).error);
  }
  if (failed) return NextResponse.redirect(`${origin}/login?error=link`);

  const { data } = await supabase.auth.getUser();
  if (!(await matchesOwnerEmail(supabase, data.user?.email))) return NextResponse.redirect(`${origin}/unauthorized`);
  return NextResponse.redirect(`${origin}${next}`);
}
