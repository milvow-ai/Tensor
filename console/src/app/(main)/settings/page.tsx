import { Settings } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { resolveDataSource } from "@/lib/farm/mode";

import { PoliciesView } from "../policies/_components/policies-view";
import { loadPolicies } from "../policies/_lib/load-policies";
import { RoutingView } from "../routing/_components/routing-view";
import { loadRouting } from "../routing/_lib/load-routing";
import { SettingsTabs } from "./_components/settings-tabs";
import { SettingsView } from "./_components/settings-view";
import { parseSettingsTab } from "./_lib/tabs";

export const metadata: Metadata = {
  title: "Settings",
  description: "Owner, time zone, notifications, policies, budgets and routing.",
};

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const tab = parseSettingsTab((await searchParams).tab);

  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title="Settings"
        description="Owner identity, timezone for resets, budgets, routing, alert endpoints, and runtime status."
        icon={Settings}
      />
      <SettingsTabs active={tab} />
      <TabBody tab={tab} />
    </div>
  );
}

async function TabBody({ tab }: { tab: ReturnType<typeof parseSettingsTab> }) {
  if (tab === "policies") {
    const { budgets, spendMonth, auditEvents } = await loadPolicies();
    return <PoliciesView initialBudgets={budgets} spendMonth={spendMonth} initialAuditEvents={auditEvents} />;
  }

  if (tab === "routing") {
    const { routes, accountsByPool } = await loadRouting();
    return <RoutingView initialRoutes={routes} accountsByPool={accountsByPool} />;
  }

  if (tab === "workspace") {
    const ownerEmail = process.env.OWNER_EMAIL || "owner@example.com";
    const timezone = process.env.FARM_TIMEZONE || "UTC";
    return (
      <SettingsView dataSource={resolveDataSource()} initialOwnerEmail={ownerEmail} initialTimezone={timezone} />
    );
  }

  return <PendingTab tab={tab} />;
}

function PendingTab({ tab }: { tab: ReturnType<typeof parseSettingsTab> }) {
  const copy = PENDING_COPY[tab as keyof typeof PENDING_COPY];
  return (
    <div
      data-testid={`settings-${tab}-empty`}
      className="flex flex-col items-center gap-1 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] border-dashed bg-[var(--kit-bg-card,#ffffff)] px-6 py-12 text-center dark:border-[#30363d] dark:bg-[#161b22]"
    >
      <h2 className="font-semibold text-sm">{copy?.title}</h2>
      <p className="max-w-md text-muted-foreground text-xs">{copy?.body}</p>
    </div>
  );
}

const PENDING_COPY = {
  members: {
    title: "No members to manage yet",
    body: "Invites and roles arrive with multi-user workspaces. Today the owner is the only member.",
  },
  prices: {
    title: "No price table recorded yet",
    body: "Per-model prices with source and date will appear here once the Farm records them.",
  },
  notifications: {
    title: "Notification preferences are not configurable yet",
    body: "Urgent alerts go to Telegram today; set that up under Workspace.",
  },
} as const;
