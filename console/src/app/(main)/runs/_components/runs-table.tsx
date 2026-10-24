"use client";

import { Fragment, useEffect, useState, useTransition } from "react";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ChevronLeft, ChevronRight, Play, Radio, X } from "lucide-react";

import { DayChip, FilterSelect, StatusChip } from "@/components/kit";
import { EmptyState } from "@/components/farm/states";
import { RelativeTime } from "@/components/farm/time";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { DASH, fmtAbsolute, fmtDuration, fmtUsd } from "@/lib/farm/format";
import type { Page, RunRow } from "@/lib/farm/types";

import { RunDetailDrawer } from "./run-detail-drawer";
import { KIT_CARD, KIT_TEXT_PRIMARY, KIT_TEXT_SECONDARY, RUN_CHIP } from "./run-status";

const ALL = "all";

const STATUS_OPTIONS = [
  { value: ALL, label: "All statuses" },
  { value: "succeeded", label: "Success" },
  { value: "failed", label: "Failure" },
  { value: "blocked", label: "Blocked" },
  { value: "running", label: "Running" },
];

const CALLER_OPTIONS = ["claude", "hermes", "console", "cli", "test"];

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

interface DayGroup {
  key: string;
  weekday: string;
  day: number;
  title: string;
  rows: RunRow[];
  costUsd: number;
}

/** Group rows by UTC day (stable between server and browser), keeping the incoming newest-first order. */
function groupByDay(rows: RunRow[]): DayGroup[] {
  const groups = new Map<string, DayGroup>();
  for (const r of rows) {
    const d = new Date(r.started_at);
    const key = d.toISOString().slice(0, 10);
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        weekday: WEEKDAYS[d.getUTCDay()],
        day: d.getUTCDate(),
        title: `${WEEKDAYS[d.getUTCDay()]}, ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`,
        rows: [],
        costUsd: 0,
      };
      groups.set(key, g);
    }
    g.rows.push(r);
    g.costUsd += r.cost_usd;
  }
  return Array.from(groups.values());
}

export function RunsTable({
  runsPage,
  capabilities,
  pools,
}: {
  runsPage: Page<RunRow>;
  capabilities: string[];
  pools: string[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [, startTransition] = useTransition();

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [isLive, setIsLive] = useState(true);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  // Read current filters from URL
  const currentCapability = searchParams.get("capability") ?? "";
  const currentStatus = searchParams.get("status") ?? "";
  const currentCaller = searchParams.get("caller") ?? "";
  const currentPool = searchParams.get("pool") ?? "";
  const failuresOnly = searchParams.get("failures_only") === "true";
  const currentPage = Number(searchParams.get("page") ?? "1");
  const hasFilters = Boolean(currentCapability || currentStatus || currentCaller || currentPool || failuresOnly);

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
      if (value === null || value === "" || value === ALL) {
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

  if (runsPage.total === 0 && !hasFilters) {
    return (
      <EmptyState
        icon={Play}
        title="No runs yet"
        description="Every capability request through the Farm will appear here with execution trajectories and costs."
      >
        <Button asChild>
          <Link href="/integrations">Add your first MCP server</Link>
        </Button>
      </EmptyState>
    );
  }

  const groups = groupByDay(runsPage.items);

  return (
    <>
      <div className={`${KIT_CARD} overflow-hidden`}>
        {/* Filter bar */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-[var(--kit-border,#e6e8ec)] border-b p-3 sm:p-4 dark:border-[#30363d]">
          <div className="flex flex-wrap items-center gap-2">
            <FilterSelect
              aria-label="Filter by capability"
              value={currentCapability || ALL}
              onChange={(v) => updateQuery({ capability: v })}
              options={[{ value: ALL, label: "All capabilities" }, ...capabilities.map((c) => ({ value: c, label: c }))]}
            />
            <FilterSelect
              aria-label="Filter by status"
              value={currentStatus || ALL}
              onChange={(v) => updateQuery({ status: v })}
              disabled={failuresOnly}
              options={STATUS_OPTIONS}
            />
            <FilterSelect
              aria-label="Filter by pool"
              value={currentPool || ALL}
              onChange={(v) => updateQuery({ pool: v })}
              options={[{ value: ALL, label: "All pools" }, ...pools.map((p) => ({ value: p, label: p }))]}
            />
            <FilterSelect
              aria-label="Filter by caller"
              value={currentCaller || ALL}
              onChange={(v) => updateQuery({ caller: v })}
              options={[{ value: ALL, label: "All callers" }, ...CALLER_OPTIONS.map((c) => ({ value: c, label: c }))]}
            />

            <div
              className={`flex h-8 items-center gap-2 rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] px-2.5 text-xs dark:border-[#30363d] ${KIT_TEXT_PRIMARY}`}
            >
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

            {hasFilters ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-8 gap-1 text-muted-foreground text-xs"
                onClick={() =>
                  updateQuery({ capability: null, status: null, caller: null, pool: null, failures_only: null })
                }
              >
                <X className="size-3" />
                <span>Clear</span>
              </Button>
            ) : null}
          </div>

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

        {/* Stream, grouped by day */}
        <Table data-testid="runs-table">
          <TableHeader>
            <TableRow>
              <TableHead>Capability</TableHead>
              <TableHead>Outcome</TableHead>
              <TableHead>Caller</TableHead>
              <TableHead>Pool & account</TableHead>
              <TableHead className="text-right">Duration</TableHead>
              <TableHead className="text-right">Cost</TableHead>
              <TableHead className="text-right">Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {groups.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className={`h-28 text-center text-sm ${KIT_TEXT_SECONDARY}`}>
                  No runs match the selected filters.
                </TableCell>
              </TableRow>
            ) : (
              groups.map((g) => {
                const isCollapsed = collapsed[g.key] === true;
                return (
                  <Fragment key={g.key}>
                  <TableRow className="bg-[var(--kit-bg-subtle,#f3f4f6)] hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#0d1117]">
                    <TableCell colSpan={7} className="py-2">
                      <div className="flex items-center gap-3">
                        <DayChip
                          size="sm"
                          label={g.weekday}
                          dayNumber={g.day}
                          isSelected={!isCollapsed}
                          aria-label={`${g.title}, ${isCollapsed ? "show" : "hide"} ${g.rows.length} runs`}
                          onClick={() => setCollapsed((c) => ({ ...c, [g.key]: !isCollapsed }))}
                        />
                        <div className="flex flex-col text-xs">
                          <span className={`font-semibold ${KIT_TEXT_PRIMARY}`}>{g.title} (UTC)</span>
                          <span className={`tabular-nums ${KIT_TEXT_SECONDARY}`}>
                            {g.rows.length} run{g.rows.length === 1 ? "" : "s"} · {fmtUsd(g.costUsd)}
                          </span>
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                  {isCollapsed
                    ? null
                    : g.rows.map((r) => {
                        const chip = RUN_CHIP[r.status];
                        return (
                          <TableRow
                            key={r.id}
                            className="cursor-pointer hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:hover:bg-[#21262d]"
                            onClick={() => handleRowClick(r.id)}
                            data-testid={`run-row-${r.id}`}
                          >
                            <TableCell className="font-medium font-mono text-xs">
                              <div className="flex flex-col gap-0.5">
                                <span>{r.capability}</span>
                                {r.status === "failed" && r.error_kind ? (
                                  <span className="font-normal text-[11px] text-[var(--kit-negative-text,#b91c1c)] dark:text-[#ff7b72]">
                                    {r.error_kind}
                                  </span>
                                ) : null}
                              </div>
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap items-center gap-1.5">
                                <StatusChip size="sm" status={chip.status} label={chip.label} />
                                {r.cached ? (
                                  <span className={`text-[11px] ${KIT_TEXT_SECONDARY}`}>Cached</span>
                                ) : null}
                              </div>
                            </TableCell>
                            <TableCell className={`font-mono text-xs ${KIT_TEXT_SECONDARY}`}>{r.caller}</TableCell>
                            <TableCell className="text-xs">
                              <div className="flex flex-col">
                                <span>{r.provider_name ?? (r.cached ? "Cache" : DASH)}</span>
                                {r.connection_label ? (
                                  <span className={`font-mono text-[11px] ${KIT_TEXT_SECONDARY}`}>
                                    {r.connection_label}
                                  </span>
                                ) : null}
                              </div>
                            </TableCell>
                            <TableCell
                              className={`text-right font-mono text-xs tabular-nums ${KIT_TEXT_SECONDARY}`}
                            >
                              {fmtDuration(r.duration_ms)}
                            </TableCell>
                            <TableCell className="text-right font-mono text-xs tabular-nums">
                              {fmtUsd(r.cost_usd)}
                            </TableCell>
                            <TableCell
                              className={`text-right text-xs ${KIT_TEXT_SECONDARY}`}
                              title={fmtAbsolute(r.started_at, true)}
                            >
                              <RelativeTime iso={r.started_at} />
                            </TableCell>
                          </TableRow>
                        );
                      })}
                  </Fragment>
                );
              })
            )}
          </TableBody>
        </Table>

        {/* Pagination */}
        <div
          className={`flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-t px-4 py-3 text-xs dark:border-[#30363d] ${KIT_TEXT_SECONDARY}`}
        >
          <div>
            Showing <span className={`font-medium ${KIT_TEXT_PRIMARY}`}>{runsPage.items.length}</span> of{" "}
            <span className={`font-medium ${KIT_TEXT_PRIMARY}`}>{runsPage.total}</span> runs
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
      </div>

      {/* Run trace drawer */}
      <RunDetailDrawer runId={selectedRunId} open={drawerOpen} onOpenChange={setDrawerOpen} />
    </>
  );
}
