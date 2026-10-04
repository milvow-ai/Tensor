import Link from "next/link";

import { ChevronRight } from "lucide-react";

import { SectionTitle } from "@/components/farm/section-title";
import { DotList, ToneBadge } from "@/components/farm/status";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { fmtCompact } from "@/lib/farm/format";
import { HEALTH_META, poolHref } from "@/lib/farm/state";
import type { CapabilityCapacityRow } from "@/lib/farm/types";

interface Route {
  capability: string;
  description: string;
  kind: "tool" | "ai";
  strategy: string | null;
  steps: CapabilityCapacityRow[];
}

function toRoutes(rows: CapabilityCapacityRow[]): Route[] {
  const routes = new Map<string, Route>();
  for (const row of rows) {
    if (!row.route_enabled) continue;
    const route = routes.get(row.capability) ?? {
      capability: row.capability,
      description: row.description,
      kind: row.kind,
      strategy: row.default_strategy,
      steps: [],
    };
    route.steps.push(row);
    routes.set(row.capability, route);
  }
  for (const route of routes.values()) route.steps.sort((a, b) => a.route_position - b.route_position);
  return [...routes.values()];
}

/**
 * How a request flows: capability, then the pools tried in order (each one a fallback for the one before),
 * then the accounts inside each pool. Built from the route rows; plain markup, no chart library.
 */
export function FlowMap({ rows }: { rows: CapabilityCapacityRow[] }) {
  const routes = toRoutes(rows);
  return (
    <Card className="gap-3">
      <CardHeader>
        <SectionTitle>How a request flows</SectionTitle>
        <CardDescription>
          A capability goes to its first pool; if that pool cannot serve it, the router moves right. Inside a pool the
          strategy picks the account.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="divide-y">
          {routes.map((route) => (
            <li key={route.capability} className="grid gap-3 py-3 first:pt-0 last:pb-0 lg:grid-cols-[13rem_1fr]">
              <div className="min-w-0">
                <div className="font-medium font-mono text-[13px]">{route.capability}</div>
                <p className="mt-0.5 line-clamp-2 text-muted-foreground text-xs">{route.description}</p>
                <div className="mt-1 text-muted-foreground text-xs">
                  strategy <span className="font-mono text-foreground">{route.strategy ?? "default"}</span>
                </div>
              </div>
              <ol className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-stretch">
                {route.steps.map((step, index) => {
                  const health = HEALTH_META[step.health];
                  return (
                    <li key={step.provider_id} className="flex items-center gap-2">
                      {index > 0 ? (
                        <ChevronRight
                          aria-hidden="true"
                          className="hidden size-4 shrink-0 text-muted-foreground sm:block"
                        />
                      ) : null}
                      <Link
                        href={poolHref({ provider_id: step.provider_id, kind: route.kind })}
                        className="block min-w-44 flex-1 rounded-lg border bg-background px-3 py-2 transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring sm:flex-none"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex items-center gap-1.5 font-medium text-[13px]">
                            <span className="flex size-4 items-center justify-center rounded-full bg-muted font-medium text-[10px] tabular-nums">
                              {index + 1}
                            </span>
                            {step.provider_name}
                          </span>
                          <ToneBadge tone={health.tone} dot={false} className="h-4 px-1.5 text-[10px]">
                            {health.label}
                          </ToneBadge>
                        </div>
                        <div className="mt-1.5">
                          <DotList dots={step.account_dots} label={`${step.provider_name} accounts`} size="sm" />
                        </div>
                        <div className="mt-1.5 text-muted-foreground text-xs tabular-nums">
                          {step.unlimited ? "Unlimited" : `${fmtCompact(step.remaining_calls)} calls`} ·{" "}
                          {step.accounts_usable}/{step.accounts_total} usable
                        </div>
                      </Link>
                    </li>
                  );
                })}
              </ol>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
