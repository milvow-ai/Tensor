"use client";

import { useMemo } from "react";

import { AlertCircle, Calendar, ExternalLink } from "lucide-react";

import { SectionTitle } from "@/components/farm/section-title";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { fmtAbsolute, fmtUsd } from "@/lib/farm/format";
import type { RenewalRow } from "@/lib/farm/types";

export function RenewalCalendar({ renewals }: { renewals: RenewalRow[] }) {
  const groups = useMemo(() => {
    const weekMap: Record<string, RenewalRow[]> = {
      "This week (0–7 days)": [],
      "Next week (8–14 days)": [],
      "In 2–3 weeks (15–21 days)": [],
      "In 3–4 weeks (22–28 days)": [],
      "Later (29–45 days)": [],
    };

    renewals.forEach((r) => {
      const days = r.days_until_renewal;
      if (days <= 7) {
        weekMap["This week (0–7 days)"].push(r);
      } else if (days <= 14) {
        weekMap["Next week (8–14 days)"].push(r);
      } else if (days <= 21) {
        weekMap["In 2–3 weeks (15–21 days)"].push(r);
      } else if (days <= 28) {
        weekMap["In 3–4 weeks (22–28 days)"].push(r);
      } else {
        weekMap["Later (29–45 days)"].push(r);
      }
    });

    return Object.entries(weekMap)
      .filter(([, items]) => items.length > 0)
      .map(([weekLabel, items]) => ({ weekLabel, items }));
  }, [renewals]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <SectionTitle>Renewal calendar (next 45 days)</SectionTitle>
            <CardDescription>
              Upcoming plan renewals in the next 45 days. Low-usage plans (&lt;20%) are flagged for cancellation.
            </CardDescription>
          </div>
          <div className="flex items-center gap-1.5 text-muted-foreground text-xs">
            <Calendar className="size-4" />
            <span>Next 45 days</span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        {groups.length === 0 ? (
          <p className="py-6 text-center text-muted-foreground text-sm">
            No upcoming renewals found in the next 45 days.
          </p>
        ) : (
          groups.map(({ weekLabel, items }) => (
            <div key={weekLabel} className="flex flex-col gap-2.5">
              <h3 className="font-semibold text-muted-foreground text-xs uppercase tracking-wider">
                {weekLabel} ({items.length})
              </h3>
              <div className="divide-y rounded-lg border bg-card/50">
                {items.map((r) => {
                  const isUnderUtilized = r.usage_pct !== null && r.usage_pct < 20;

                  return (
                    <div
                      key={r.connection_id}
                      className="flex flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between"
                      data-testid={`renewal-item-${r.connection_id}`}
                    >
                      <div className="flex flex-col gap-0.5">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm">{r.provider_name}</span>
                          <span className="text-muted-foreground text-xs">·</span>
                          <span className="font-mono text-xs">{r.connection_label}</span>
                          {r.plan_name ? (
                            <span className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium text-foreground">
                              {r.plan_name}
                            </span>
                          ) : null}
                        </div>
                        <div className="flex items-center gap-2 text-muted-foreground text-xs">
                          <span>Renews {fmtAbsolute(r.renews_on, false)}</span>
                          <span>·</span>
                          <span className="font-medium text-foreground tabular-nums">
                            in {r.days_until_renewal} {r.days_until_renewal === 1 ? "day" : "days"}
                          </span>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-4 sm:justify-end">
                        {/* Usage % & warning */}
                        <div className="flex flex-col items-start sm:items-end">
                          <div className="flex items-center gap-1.5">
                            <span className="text-muted-foreground text-xs">Usage:</span>
                            <span className="font-mono font-medium text-xs tabular-nums">
                              {r.usage_pct !== null ? `${r.usage_pct}%` : "Unlimited"}
                            </span>
                          </div>
                          {isUnderUtilized ? (
                            <div className="mt-0.5 flex items-center gap-1 text-[11px] font-medium text-amber-800 dark:text-amber-400">
                              <AlertCircle className="size-3" />
                              <span>Usage: {r.usage_pct}% · Consider cancelling</span>
                            </div>
                          ) : null}
                        </div>

                        {/* Price */}
                        <div className="font-mono font-semibold text-sm tabular-nums">
                          {fmtUsd(r.price_usd, { whole: true })}
                          <span className="font-normal text-muted-foreground text-xs">/mo</span>
                        </div>

                        {/* Human Task Cancellation Link */}
                        <Button
                          variant="outline"
                          size="sm"
                          asChild
                          className="h-8 gap-1.5 text-xs"
                        >
                          <a
                            href={`https://${r.provider_id}.com/account/billing`}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label="Open cancellation task"
                            title="Open provider dashboard to manage or cancel plan (Human task, never automated)"
                          >
                            <span>Open cancellation task</span>
                            <ExternalLink className="size-3" />
                          </a>
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
