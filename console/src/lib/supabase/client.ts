import { createBrowserClient } from "@supabase/ssr";

import { requireSupabaseEnv } from "./env";

/** Browser client: used only for the magic-link sign-in. Never reads Farm data. */
export function createClient() {
  const { url, key } = requireSupabaseEnv();
  return createBrowserClient(url, key);
}
