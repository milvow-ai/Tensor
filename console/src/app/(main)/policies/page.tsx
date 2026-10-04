import { ShieldCheck } from "lucide-react";
import type { Metadata } from "next";

import { ComingSoon } from "@/components/farm/coming-soon";

export const metadata: Metadata = { title: "Policies & Budgets" };

export default function PoliciesPage() {
  return (
    <ComingSoon
      title="Policies & Budgets"
      description="Spending caps, hard stops and alert thresholds."
      milestone="C3"
      icon={ShieldCheck}
      will={[
        "Set a monthly cap per provider, per account and for the whole Farm, with an optional hard stop.",
        "Choose the usage levels that raise alerts (50, 80 and 100 percent by default).",
        "See which calls a policy blocked and why.",
      ]}
      today={{
        text: "Budgets already apply in the Farm. Their effect shows as blocked runs and budget alerts on Overview.",
        href: "/overview",
        label: "Open Overview",
      }}
    />
  );
}
