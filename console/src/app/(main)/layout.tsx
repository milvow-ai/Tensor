import type { ReactNode } from "react";
import { cookies } from "next/headers";

import { NowProvider } from "@/components/farm/time";
import { requireOwner } from "@/lib/auth";
import { attempt } from "@/lib/farm/data";

import { MainShell } from "./_components/main-shell";

export default async function Layout({ children }: Readonly<{ children: ReactNode }>) {
  const user = await requireOwner();
  const cookieStore = await cookies();
  const defaultCollapsed = cookieStore.get("sidebar_collapsed")?.value === "true";

  const alertsResult = await attempt((data) => data.listAlerts());
  const openAlertsCount = alertsResult.ok
    ? alertsResult.data.filter((alert) => alert.acked_at === null).length
    : 0;

  return (
    <MainShell
      user={user}
      defaultCollapsed={defaultCollapsed}
      openAlertsCount={openAlertsCount}
    >
      <NowProvider value={Date.now()}>{children}</NowProvider>
    </MainShell>
  );
}
