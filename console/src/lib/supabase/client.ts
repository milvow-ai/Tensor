import { createBrowserClient } from "@supabase/ssr";

import { requireSupabaseEnv, type SupabaseEnv } from "./env";

/**
 * Browser client: used only for the magic-link sign-in. Never reads Farm data. The login page hands over the
 * runtime values, so a build made without the NEXT_PUBLIC_ variables still signs in where the server has them.
 */
export function createClient(env: SupabaseEnv = requireSupabaseEnv()) {
  const { url, key } = env;
  return createBrowserClient(url, key);
}
