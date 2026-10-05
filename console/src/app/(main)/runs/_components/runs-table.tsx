"use client";

import { useEffect, useState, useTransition } from "react";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ChevronLeft, ChevronRight, Radio, X } from "lucide-react";

import { ToneBadge } from "@/components/farm/status";
import { RelativeTime } from "@/components/farm/time";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DASH, fmtAbsolute, fmtDuration, fmtUsd } from "@/lib/farm/format";
import { RUN_STATUS_META } from "@/lib/farm/state";
import type { Page, RunRow } from "@/lib/farm/types";

import { RunDetailDrawer } from "./run-detail-drawer";

export function RunsTable({ runsPage, capabilities }: { runsPage: Page<RunRow>; capabilities: string[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [, startTransition] = useTransition();

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [isLive, setIsLive] = useState(true);

  // Read current filters from URL
  const currentCapability = searchParams.get("capability") ?? "";
  const currentStatus = searchParams.get("status") ?? "";
  const currentCaller = searchParams.get("caller") ?? "";
  const failuresOnly = searchParams.get("failures_only") === "true";
  const currentPage = Number(searchParams.get("page") ?? "1");

  // 5-second realtime poll while page is open
  useEffect(() => {
    if (!isLive) return;
    const interval = setInterval(() => {
      startTransition(() => {
        router.refresh();
      });
    }, 5000);
    return () => clearInterval(interval);
  }, [isLive, router]);

  function updateQuery(updates: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value === null || value === "") {
        params.delete(key);
      } else {
        params.set(key, value);
      }
    }
    // Always reset page to 1 on filter changes unless changing page itself
    if (!("page" in updates)) {
      params.delete("page");
    }
    startTransition(() => {
      router.push(`${pathname}?${params.toString()}`);
    });
  }

  function handleRowClick(runId: string) {
    setSelectedRunId(runId);
    setDrawerOpen(true);
  }

  const totalPages = Math.max(1, Math.ceil(runsPage.total / runsPage.pageSize));

  return (
    <>
      <Card>
        <CardHeader className="pb-4">
          <div className="flex flex-col gap-4">
            {/* Filter Bar */}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap items-center gap-2.5">
                {/* Capability filter */}
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground text-xs">Capability:</span>
                  <NativeSelect
                    value={currentCapability}
                    onChange={(e) => updateQuery({ capability: e.target.value })}
                    className="h-8 font-mono text-xs"
                    aria-label="Filter by capability"
                  >
                    <option value="">All capabilities</option>
                    {capabilities.map((cap) => (
                      <option key={cap} value={cap}>
                        {cap}
                      </option>
                    ))}
                  </NativeSelect>
                </div>

                {/* Status filter */}
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground text-xs">Status:</span>
                  <NativeSelect
                    value={currentStatus}
                    onChange={(e) => updateQuery({ status: e.target.value })}
                    className="h-8 text-xs"
                    disabled={failuresOnly}
                    aria-label="Filter by status"
                  >
                    <option value="">All statuses</option>
                    <option value="succeeded">Success</option>
                    <option value="failed">Failure</option>
                    <option value="blocked">Blocked</option>
                    <option value="running">Running</option>
                  </NativeSelect>
                </div>

                {/* Caller filter */}
                <div className="flex items-center gap-1.5">
                  <span className="text-muted-foreground text-xs">Caller:</span>
                  <NativeSelect
                    value={currentCaller}
                    onChange={(e) => updateQuery({ caller: e.target.value })}
                    className="h-8 font-mono text-xs"
                    aria-label="Filter by caller"
                  >
                    <option value="">All callers</option>
                    <option value="claude">claude</option>
                    <option value="hermes">hermes</option>
                    <option value="console">console</option>
                    <option value="cli">cli</option>
                    <option value="test">test</option>
                  </NativeSelect>
                </div>

                {/* Failures Only Toggle */}
                <div className="flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs">
                  <label htmlFor="failures-only-toggle" className="cursor-pointer font-medium">
                    Failures only
                  </label>
                  <Switch
                    id="failures-only-toggle"
                    data-testid="failures-only-switch"
                    checked={failuresOnly}
                    onCheckedChange={(checked) => updateQuery({ failures_only: checked ? "true" : null, status: null })}
                  />
                </div>

                {/* Clear filters button */}
                {currentCapability || currentStatus || currentCaller || failuresOnly ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 gap-1 text-muted-foreground text-xs"
                    onClick={() =>
                      updateQuery({
                        capability: null,
                        status: null,
                        caller: null,
                        failures_only: null,
                      })
                    }
                  >
                    <X className="size-3" />
                    <span>Clear</span>
                  </Button>
                ) : null}
              </div>

              {/* Realtime 5s Poller Toggle */}
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 text-xs"
                  onClick={() => setIsLive(!isLive)}
                  title={isLive ? "Pause 5-second polling" : "Resume 5-second live polling"}
                >
                  <Radio className={`size-3 ${isLive ? "animate-pulse text-emerald-500" : "text-muted-foreground"}`} />
                  <span>{isLive ? "Live (5s poll)" : "Paused"}</span>
                </Button>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <Table data-testid="runs-table">
            <TableHeader>
              <TableRow>
                <TableHead>Capability</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Caller</TableHead>
                <TableHead>Pool & Account</TableHead>
                <TableHead>Cached</TableHead>
                <TableHead className="text-right">Cost</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="text-right">Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runsPage.items.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} className="h-28 text-center text-muted-foreground text-sm">
                    No runs match the selected filters.
                  </TableCell>
                </TableRow>
              ) : (
                runsPage.items.map((r) => {
                  const meta = RUN_STATUS_META[r.status];
                  return (
                    <TableRow
                      key={r.id}
                      className="cursor-pointer hover:bg-muted/50"
                      onClick={() => handleRowClick(r.id)}
                      data-testid={`run-row-${r.id}`}
                    >
                      <TableCell className="font-medium font-mono text-xs">{r.capability}</TableCell>
                      <TableCell>
                        <ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>
                      </TableCell>
                      <TableCell className="font-mono text-muted-foreground text-xs">{r.caller}</TableCell>
                      <TableCell className="text-xs">
                        <div className="flex flex-col">
                          <span>{r.provider_name ?? (r.cached ? "Cache" : DASH)}</span>
                          {r.connection_label ? (
                            <span className="font-mono text-[11px] text-muted-foreground">{r.connection_label}</span>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell>
                        {r.cached ? (
                          <ToneBadge tone="info">Cached</ToneBadge>
                        ) : (
                          <span className="text-muted-foreground text-xs">Live</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">{fmtUsd(r.cost_usd)}</TableCell>
                      <TableCell className="text-right font-mono text-muted-foreground text-xs tabular-nums">
                        {fmtDuration(r.duration_ms)}
                      </TableCell>
                      <TableCell
                        className="text-right text-muted-foreground text-xs"
                        title={fmtAbsolute(r.started_at, true)}
                      >
                        <RelativeTime iso={r.started_at} />
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>

          {/* Pagination Controls */}
          <div className="flex items-center justify-between border-t px-4 py-3 text-muted-foreground text-xs">
            <div>
              Showing <span className="font-medium text-foreground">{runsPage.items.length}</span> of{" "}
              <span className="font-medium text-foreground">{runsPage.total}</span> runs
            </div>
            <div className="flex items-center gap-2">
              <span className="tabular-nums">
                Page {currentPage} of {totalPages}
              </span>
              <Button
                variant="outline"
                size="icon-xs"
                disabled={currentPage <= 1}
                onClick={() => updateQuery({ page: String(currentPage - 1) })}
                aria-label="Previous page"
              >
                <ChevronLeft className="size-3.5" />
              </Button>
              <Button
                variant="outline"
                size="icon-xs"
                disabled={currentPage >= totalPages}
                onClick={() => updateQuery({ page: String(currentPage + 1) })}
                aria-label="Next page"
              >
                <ChevronRight className="size-3.5" />
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Run Detail Sheet Drawer */}
      <RunDetailDrawer runId={selectedRunId} open={drawerOpen} onOpenChange={setDrawerOpen} />
    </>
  );
}
