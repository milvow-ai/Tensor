"use client";

import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { type ChartConfig, ChartContainer, ChartTooltip } from "@/components/ui/chart";
import { fmtCompact, fmtInt } from "@/lib/farm/format";

import type { CapacityRow } from "./capacity-model";

const MAX_POOLS = 4;
const SERIES = ["p1", "p2", "p3", "p4"] as const;

const chartConfig = {
  p1: { label: "First choice", color: "var(--chart-1)" },
  p2: { label: "Second", color: "var(--chart-2)" },
  p3: { label: "Third", color: "var(--chart-3)" },
  p4: { label: "Fourth", color: "var(--chart-5)" },
} satisfies ChartConfig;

type Datum = Record<string, string | number | boolean>;

function toData(rows: CapacityRow[]): Datum[] {
  return rows.map((row) => {
    const datum: Datum = { capability: row.capability, total: row.total, unlimited: row.unlimited };
    row.pools.slice(0, MAX_POOLS).forEach((pool, index) => {
      datum[`p${index + 1}`] = pool.remaining;
      datum[`p${index + 1}Name`] = pool.providerName;
    });
    return datum;
  });
}

interface TickProps {
  x?: number;
  y?: number;
  payload?: { value: string };
  totals: Record<string, { total: number; unlimited: boolean }>;
}

function CapabilityTick({ x = 0, y = 0, payload, totals }: TickProps) {
  const name = payload?.value ?? "";
  const info = totals[name];
  return (
    <g transform={`translate(${x},${y})`}>
      <text x={-8} y={-3} textAnchor="end" className="fill-foreground font-medium text-xs">
        {name}
      </text>
      <text x={-8} y={11} textAnchor="end" className="fill-muted-foreground text-[11px]">
        {info ? `${fmtInt(info.total)} calls${info.unlimited ? " + unlimited" : ""}` : ""}
      </text>
    </g>
  );
}

interface TooltipPayload {
  payload?: Datum;
}

function CapacityTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayload[] }) {
  const datum = payload?.[0]?.payload;
  if (!active || !datum) return null;
  return (
    <div className="min-w-44 rounded-lg border bg-popover px-3 py-2 text-popover-foreground text-xs shadow-md">
      <div className="mb-1 font-medium">{String(datum.capability)}</div>
      <ul className="space-y-1">
        {SERIES.map((key, index) => {
          const name = datum[`${key}Name`];
          if (typeof name !== "string") return null;
          return (
            <li key={key} className="flex items-center justify-between gap-4">
              <span className="flex items-center gap-1.5">
                <span aria-hidden="true" className="size-2 rounded-sm" style={{ background: chartConfig[key].color }} />
                {index + 1}. {name}
              </span>
              <span className="tabular-nums">{fmtInt(Number(datum[key]))} calls</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function CapacityChart({ rows }: { rows: CapacityRow[] }) {
  const data = toData(rows);
  const totals = Object.fromEntries(
    rows.map((row) => [row.capability, { total: row.total, unlimited: row.unlimited }]),
  );
  const height = Math.max(rows.length * 46 + 36, 120);

  return (
    <ChartContainer config={chartConfig} className="aspect-auto w-full" style={{ height }}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 8 }} barCategoryGap={10}>
        <CartesianGrid horizontal={false} strokeDasharray="3 3" />
        <XAxis type="number" tickLine={false} axisLine={false} tickFormatter={(value: number) => fmtCompact(value)} />
        <YAxis
          type="category"
          dataKey="capability"
          tickLine={false}
          axisLine={false}
          width={132}
          tick={<CapabilityTick totals={totals} />}
        />
        <ChartTooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<CapacityTooltip />} />
        {SERIES.map((key, index) => (
          <Bar
            key={key}
            dataKey={key}
            stackId="route"
            fill={`var(--color-${key})`}
            isAnimationActive={false}
            radius={index === 0 ? [4, 0, 0, 4] : 0}
            maxBarSize={22}
          />
        ))}
      </BarChart>
    </ChartContainer>
  );
}
