import type { ReactNode } from "react";

import { FarmMark } from "@/components/farm/brand";
import { APP_CONFIG } from "@/config/app-config";

/** Shell for pages visitors can see before signing in: a quiet centered card. */
export default function PublicLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-muted/40 px-4 py-10">
      <div className="mb-6 flex items-center gap-2.5">
        <FarmMark className="size-9" />
        <div className="leading-tight">
          <div className="font-semibold text-base tracking-tight">{APP_CONFIG.name}</div>
          <div className="text-muted-foreground text-xs">Control room</div>
        </div>
      </div>
      <div className="w-full max-w-sm">{children}</div>
      <p className="mt-6 max-w-sm text-balance text-center text-muted-foreground text-xs">
        Private to the Farm owner. Provider keys never pass through this app; they stay in the PC's .env.
      </p>
    </main>
  );
}
