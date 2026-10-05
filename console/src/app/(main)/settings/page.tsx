import { Settings } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { resolveDataSource } from "@/lib/farm/mode";

import { SettingsView } from "./_components/settings-view";

export const metadata: Metadata = {
  title: "Settings",
  description: "Owner, time zone, notifications, and Farm engine runtime settings.",
};

export default function SettingsPage() {
  const dataSource = resolveDataSource();
  const ownerEmail = process.env.OWNER_EMAIL || "owner@example.com";
  const timezone = process.env.FARM_TIMEZONE || "UTC";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Owner identity, timezone for resets, alert endpoints, and runtime status."
        icon={Settings}
      />
      <SettingsView dataSource={dataSource} initialOwnerEmail={ownerEmail} initialTimezone={timezone} />
    </div>
  );
}
