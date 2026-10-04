"use client";

import dynamic from "next/dynamic";

import { SectionTitle } from "@/components/farm/section-title";
import { Card, CardAction, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import type { CapacityRow } from "./capacity-model";

// Recharts is the heaviest dependency on the page; load it after first paint so the route's first-load JS stays small.
const CapacityChart = dynamic(() => import("./capacity-chart").then((module) => module.CapacityChart), {
  ssr: false,
  loading: () => <Skeleton className="h-56 w-full" />,
});

const LEGEND = [
  { label: "First choice", color: "var(--chart-1)" },
  { label: "Second", color: "var(--chart-2)" },
  { label: "Third", color: "var(--chart-3)" },
  { label: "Fourth", color: "var(--chart-5)" },
];

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground text-xs">
      {LEGEND.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2 rounded-sm" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

function NoRows({ what }: { what: string }) {
  return <p className="py-8 text-center text-muted-foreground text-sm">No {what} capabilities are routed yet.</p>;
}

export function CapacityCard({ rows }: { rows: CapacityRow[] }) {
  const tools = rows.filter((row) => row.kind === "tool");
  const ai = rows.filter((row) => row.kind === "ai");

  return (
    <Card className="gap-3">
      <Tabs defaultValue={tools.length > 0 ? "tool" : "ai"} className="gap-3">
        <CardHeader>
          <SectionTitle>Capacity per capability</SectionTitle>
          <CardDescription>
            Calls left this period, split by pool in route order. The router tries the first choice first.
          </CardDescription>
          <CardAction>
            <TabsList>
              <TabsTrigger value="tool">Tools</TabsTrigger>
              <TabsTrigger value="ai">AI</TabsTrigger>
            </TabsList>
          </CardAction>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Legend />
          <TabsContent value="tool">
            {tools.length > 0 ? <CapacityChart rows={tools} /> : <NoRows what="tool" />}
          </TabsContent>
          <TabsContent value="ai">{ai.length > 0 ? <CapacityChart rows={ai} /> : <NoRows what="AI" />}</TabsContent>
        </CardContent>
      </Tabs>
    </Card>
  );
}
