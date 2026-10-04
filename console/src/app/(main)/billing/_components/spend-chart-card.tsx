"use client";

import dynamic from "next/dynamic";

import { SectionTitle } from "@/components/farm/section-title";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { SpendDailyRow } from "@/lib/farm/types";

// Recharts is heavy; lazy-load client-side to keep route first-load bundle under 250 kB
const SpendAreaChart = dynamic(() => import("./spend-area-chart").then((mod) => mod.SpendAreaChart), {
  ssr: false,
  loading: () => <Skeleton className="h-72 w-full rounded-md" />,
});

export function SpendChartCard({ rows }: { rows: SpendDailyRow[] }) {
  return (
    <Card>
      <CardHeader>
        <SectionTitle>Spend over time</SectionTitle>
        <CardDescription>Daily stacked spend across all tools and AI model usage.</CardDescription>
      </CardHeader>
      <CardContent>
        <SpendAreaChart rows={rows} />
      </CardContent>
    </Card>
  );
}
