"use client";

import { AlertTriangle, Pause } from "lucide-react";

import { useSharedCommands } from "@/components/farm/commands-provider";
import { SectionTitle } from "@/components/farm/section-title";
import { ToneBadge } from "@/components/farm/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtAbsolute, fmtUsd } from "@/lib/farm/format";
import type { IdlePaidRow } from "@/lib/farm/types";

export function IdlePaidCard({ rows }: { rows: IdlePaidRow[] }) {
  const { run, pending } = useSharedCommands();

  function pauseAccount(connectionId: string, label: string) {
    void run(
      "pause",
      { connection_id: connectionId, reason: "Idle paid account paused from billing console" },
      { key: connectionId, label: `Pause ${label}` },
    );
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <SectionTitle>Idle paid accounts (14+ days)</SectionTitle>
            <CardDescription>
              Paid connections that have had zero successful calls in the last 14 days. Pause them to avoid wasted
              spend.
            </CardDescription>
          </div>
          {rows.length > 0 ? (
            <div className="flex items-center gap-1.5 font-medium text-amber-800 text-xs dark:text-amber-400">
              <AlertTriangle className="size-4" />
              <span>{rows.length} idle account(s)</span>
            </div>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Account</TableHead>
              <TableHead>Plan & Price</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Last Success</TableHead>
              <TableHead className="text-right">Action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={5} className="h-24 text-center text-muted-foreground text-sm">
                  No idle paid accounts found. Every paid account is actively utilized.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((r) => {
                const isPending = Boolean(pending[r.connection_id]);
                const isPaused = r.status === "paused";

                return (
                  <TableRow key={r.connection_id} data-testid={`idle-account-${r.connection_id}`}>
                    <TableCell>
                      <div className="flex flex-col">
                        <div className="flex items-center gap-1.5">
                          <span className="font-medium text-sm">{r.provider_name}</span>
                          <span className="font-mono text-muted-foreground text-xs">({r.connection_id})</span>
                        </div>
                        <span className="font-mono text-muted-foreground text-xs">{r.connection_label}</span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5 text-xs">
                        <span className="font-medium">{r.plan_name ?? "Paid"}</span>
                        <span className="font-mono font-semibold tabular-nums">
                          {fmtUsd(r.plan_price_usd, { whole: true })}/mo
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      {(() => {
                        if (isPaused) return <ToneBadge tone="info">Paused</ToneBadge>;
                        if (r.status === "needs_login") return <ToneBadge tone="bad">Needs Login</ToneBadge>;
                        return <ToneBadge tone="warn">Active (Idle)</ToneBadge>;
                      })()}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {r.last_success_at ? (
                        <div className="flex flex-col">
                          <span>{fmtAbsolute(r.last_success_at, true)}</span>
                          <span className="text-[11px] text-amber-800 dark:text-amber-400">
                            {r.days_idle.toFixed(0)} days idle
                          </span>
                        </div>
                      ) : (
                        <span className="font-medium text-amber-800 dark:text-amber-400">Never recorded</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {isPaused ? (
                        <span className="font-mono text-muted-foreground text-xs">Paused</span>
                      ) : (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={isPending}
                          aria-label="Pause account"
                          onClick={() => pauseAccount(r.connection_id, r.connection_id)}
                          className="h-8 gap-1.5 border-amber-600/30 text-amber-700 text-xs hover:bg-amber-500/10 hover:text-amber-800 dark:text-amber-300"
                        >
                          <Pause className="size-3" />
                          <span>{isPending ? "Pausing..." : "Pause"}</span>
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
