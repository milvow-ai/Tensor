import { CreditCard } from "lucide-react";
import type { Metadata } from "next";

import { ComingSoon } from "@/components/farm/coming-soon";

export const metadata: Metadata = { title: "Billing" };

export default function BillingPage() {
  return (
    <ComingSoon
      title="Billing"
      description="Budgets, spend, forecast and renewals across all accounts."
      milestone="C2"
      icon={CreditCard}
      will={[
        "See month-to-date spend per provider against its budget, with the month-end forecast.",
        "Watch a renewal calendar: which plan renews when, at what usage, keep or cancel.",
        "Compare cost per result per account and spot paid accounts that sit idle.",
      ]}
      today={{
        text: "Spend against the global budget and its forecast are on Overview. Plan price and billing day are in every account row.",
        href: "/pools/tools",
        label: "Open Tools & Pools",
      }}
    />
  );
}
