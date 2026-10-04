import { Settings } from "lucide-react";
import type { Metadata } from "next";

import { ComingSoon } from "@/components/farm/coming-soon";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <ComingSoon
      title="Settings"
      description="Owner, time zone and notifications."
      milestone="C3"
      icon={Settings}
      will={[
        "Change the owner email that may sign in.",
        "Set the time zone used for resets and reports.",
        "Configure where alerts are delivered, such as Telegram.",
      ]}
      today={{
        text: "Theme, layout and font preferences are in the controls at the top right of every page.",
        href: "/overview",
        label: "Open Overview",
      }}
    />
  );
}
