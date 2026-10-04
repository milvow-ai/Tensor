import { cookies } from "next/headers";

import { createServerClient } from "@supabase/ssr";

import { requireSupabaseEnv } from "./env";

/** Supabase client bound to the signed-in user's session cookies (server components, actions, route handlers). */
export async function createClient() {
  const cookieStore = await cookies();
  const { url, key } = requireSupabaseEnv();

  return createServerClient(url, key, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Called from a Server Component, where cookies are read-only. The proxy refreshes the session instead.
        }
      },
    },
  });
}
