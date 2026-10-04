import { Database } from "lucide-react";
import type { Metadata } from "next";

import { ComingSoon } from "@/components/farm/coming-soon";

export const metadata: Metadata = { title: "Memory & Evidence" };

export default function MemoryPage() {
  return (
    <ComingSoon
      title="Memory & Evidence"
      description="What the Farm has learned, and the proof behind it."
      milestone="C3"
      icon={Database}
      will={[
        "Browse entities and their facts with source account, confidence and freshness.",
        "Open the screenshots and captures that back a fact, with their hashes.",
        "See which facts are stale and what it would cost to refresh them.",
      ]}
      today={{
        text: "Facts and evidence are stored by the Farm already; this page is the viewer for them. Run history is on Overview.",
        href: "/overview",
        label: "Open Overview",
      }}
    />
  );
}
