"use client";

import { useMemo, useState } from "react";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Button } from "@/components/ui/button";
import { fmtUsd } from "@/lib/farm/format";
import type { SpendDailyRow } from "@/lib/farm/types";

const PROVIDER_COLORS: Record<string, string> = {
  clay: "#2563eb",
  claude: "#d97706",
  reoon: "#16a34a",
  zerobounce: "#0891b2",
  apollo: "#7c3aed",
  hunter: "#ea580c",
  gemini: "#4f46e5",
  codex: "#059669",
  hermes: "#db2777",
};

const FALLBACK_PALETTE = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

export function SpendAreaChart({ rows }: { rows: SpendDailyRow[] }) {
  const [rangeDays, setRangeDays] = useState<30 | 90>(30);

  const { chartData, providers } = useMemo(() => {
    // Unique providers in data
    const providerMap = new Map<string, string>();
    for (const r of rows) {
      providerMap.set(r.provider_id, r.provider_name);
    }
    const providerList = Array.from(providerMap.entries()).map(([id, name]) => ({ id, name }));

    // Filter to range
    const cutoff = new Date(Date.now() - rangeDays * 86_400_000).toISOString().slice(0, 10);
    const filtered = rows.filter((r) => r.day >= cutoff);

    // Group by day
    const dayMap = new Map<string, Record<string, number | string>>();
    for (const r of filtered) {
      let entry = dayMap.get(r.day);
      if (!entry) {
        entry = { day: r.day };
        dayMap.set(r.day, entry);
      }
      entry[r.provider_id] = Number(entry[r.provider_id] ?? 0) + Number(r.spend_usd);
    }

    const sortedDays = Array.from(dayMap.keys()).sort();
    const data = sortedDays.map((d) => dayMap.get(d) ?? { day: d });

    return { chartData: data, providers: providerList };
  }, [rows, rangeDays]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-muted-foreground text-xs">Daily spend aggregated across tools and AI providers</div>
        <div className="inline-flex rounded-lg border bg-muted/40 p-0.5">
          <Button
            size="sm"
            variant={rangeDays === 30 ? "secondary" : "ghost"}
            className="h-7 px-3 text-xs"
            onClick={() => setRangeDays(30)}
          >
            30 days
          </Button>
          <Button
            size="sm"
            variant={rangeDays === 90 ? "secondary" : "ghost"}
            className="h-7 px-3 text-xs"
            onClick={() => setRangeDays(90)}
          >
            90 days
          </Button>
        </div>
      </div>

      <div className="h-72 w-full pt-2">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -15, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" opacity={0.6} />
            <XAxis
              dataKey="day"
              tickLine={false}
              axisLine={false}
              tickFormatter={(d: string) => d.slice(5)}
              className="text-[11px]"
              stroke="var(--muted-foreground)"
            />
            <YAxis
              tickLine={false}
              axisLine={false}
              tickFormatter={(val: number) => `$${val}`}
              className="text-[11px]"
              stroke="var(--muted-foreground)"
            />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const total = payload.reduce((sum, item) => sum + Number(item.value ?? 0), 0);
                return (
                  <div className="rounded-lg border bg-popover p-2.5 text-popover-foreground text-xs shadow-md">
                    <div className="mb-1.5 font-medium">
                      {String(label)} · Total: {fmtUsd(total)}
                    </div>
                    <ul className="space-y-1">
                      {payload.map((item) => {
                        const key = typeof item.dataKey === "string" ? item.dataKey : String(item.dataKey ?? "");
                        const pName = providers.find((p) => p.id === key)?.name ?? key;
                        return (
                          <li key={key} className="flex items-center justify-between gap-4">
                            <span className="flex items-center gap-1.5">
                              <span
                                aria-hidden="true"
                                className="size-2 rounded-full"
                                style={{ backgroundColor: item.color }}
                              />
                              <span>{pName}</span>
                            </span>
                            <span className="font-mono tabular-nums">{fmtUsd(Number(item.value))}</span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                );
              }}
            />
            {providers.map((p, idx) => {
              const color = PROVIDER_COLORS[p.id] ?? FALLBACK_PALETTE[idx % FALLBACK_PALETTE.length];
              return (
                <Area
                  key={p.id}
                  type="monotone"
                  dataKey={p.id}
                  stackId="spend"
                  stroke={color}
                  fill={color}
                  fillOpacity={0.6}
                  isAnimationActive={false}
                />
              );
            })}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground text-xs">
        {providers.map((p, idx) => {
          const color = PROVIDER_COLORS[p.id] ?? FALLBACK_PALETTE[idx % FALLBACK_PALETTE.length];
          return (
            <div key={p.id} className="flex items-center gap-1.5">
              <span className="size-2 rounded-full" style={{ backgroundColor: color }} />
              <span>{p.name}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
