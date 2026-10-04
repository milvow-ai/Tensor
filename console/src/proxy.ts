import { type NextRequest, NextResponse } from "next/server";

import { resolveDataSource } from "@/lib/farm/mode";
import { updateSession } from "@/lib/supabase/session";

const PUBLIC_PATHS = ["/login", "/unauthorized", "/auth/callback", "/auth/signout"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

/**
 * Supabase mode: refresh the session and send signed-out visitors to /login. The owner-email check happens on the
 * server in the (main) layout and in every action. Fixtures mode (local development only) skips auth.
 */
export async function proxy(request: NextRequest) {
  if (resolveDataSource() === "fixtures") return NextResponse.next();

  const { response, signedIn } = await updateSession(request);
  const { pathname } = request.nextUrl;

  if (!signedIn && !isPublic(pathname)) {
    const login = request.nextUrl.clone();
    login.pathname = "/login";
    login.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + request.nextUrl.search)}`;
    return NextResponse.redirect(login);
  }
  if (signedIn && pathname === "/login") {
    const home = request.nextUrl.clone();
    home.pathname = "/overview";
    home.search = "";
    return NextResponse.redirect(home);
  }
  return response;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|.*\\.(?:png|jpg|jpeg|svg|webp|ico|txt)$).*)",
  ],
};
