"use client";

import { useState } from "react";

import { Check, Edit2, ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";

import { useSharedCommands } from "@/components/farm/commands-provider";
import { SectionTitle } from "@/components/farm/section-title";
import { ToneBadge } from "@/components/farm/status";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { isForecastOverBudget } from "@/lib/farm/forecast";
import { fmtPct, fmtUsd } from "@/lib/farm/format";
import type { BudgetRow, PoolOverviewRow } from "@/lib/farm/types";

export function BudgetsTable({ budgets, pools }: { budgets: BudgetRow[]; pools: PoolOverviewRow[] }) {
  const { run } = useSharedCommands();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string>("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const poolNameMap = new Map(pools.map((p) => [p.provider_id, p.provider_name]));

  function startEditing(budget: BudgetRow) {
    setEditingId(budget.id);
    setEditValue(String(budget.monthly_usd));
    setValidationError(null);
  }

  function cancelEditing() {
    setEditingId(null);
    setEditValue("");
    setValidationError(null);
  }

  async function saveBudget(budget: BudgetRow) {
    const trimmed = editValue.trim();
    const num = Number(trimmed);
    if (!trimmed || Number.isNaN(num)) {
      setValidationError("Please enter a valid number");
      return;
    }
    if (num < 0) {
      setValidationError("Budget cannot be negative");
      return;
    }
    if (num > 1_000_000) {
      setValidationError("Budget exceeds maximum allowed ($1,000,000)");
      return;
    }

    try {
      await run(
        "set_budget",
        {
          scope: budget.scope,
          monthly_usd: num,
          ref: budget.ref,
          hard_stop: budget.hard_stop,
        },
        {
          key: `budget:${budget.id}`,
          label: `Set ${budget.ref ? (poolNameMap.get(budget.ref) ?? budget.ref) : "Global"} budget to ${fmtUsd(num)}`,
          onDone: () => {
            setEditingId(null);
          },
          onRejected: (reason) => {
            toast.error(reason);
          },
        },
      );
    } catch {
      toast.error("Failed to enqueue budget update");
    }
  }

  function toggleHardStop(budget: BudgetRow, checked: boolean) {
    void run(
      "set_budget",
      {
        scope: budget.scope,
        monthly_usd: budget.monthly_usd,
        ref: budget.ref,
        hard_stop: checked,
      },
      {
        key: `budget-hardstop:${budget.id}`,
        label: `${checked ? "Enable" : "Disable"} hard-stop for ${budget.ref ?? "Global"}`,
      },
    );
  }

  return (
    <Card>
      <CardHeader>
        <SectionTitle>Budgets and hard stops</SectionTitle>
        <CardDescription>
          Monthly spend caps by provider and global limit. Hard stop halts routing to that pool when hit.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Scope</TableHead>
              <TableHead>Monthly Cap</TableHead>
              <TableHead>MTD Spent</TableHead>
              <TableHead>Forecast</TableHead>
              <TableHead className="text-right">Hard Stop</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {budgets.map((b) => {
              let scopeLabel = "Provider";
              if (b.scope === "global") {
                scopeLabel = "Global";
              } else if (b.ref) {
                scopeLabel = poolNameMap.get(b.ref) ?? b.ref;
              }
              const spent = b.spent_usd ?? 0;
              const forecast = b.forecast_usd ?? 0;
              const ratio = b.monthly_usd > 0 ? spent / b.monthly_usd : 0;
              const isOver = isForecastOverBudget(forecast, b.monthly_usd);
              const isEditing = editingId === b.id;

              const testKey = b.scope === "global" ? "global" : (b.ref ?? b.id);

              return (
                <TableRow key={b.id} data-testid={`budget-row-${testKey}`}>
                  <TableCell className="font-medium">
                    <div className="flex items-center gap-2">
                      <span>{scopeLabel}</span>
                      {b.scope === "global" ? <ToneBadge tone="info">All Pools</ToneBadge> : null}
                    </div>
                  </TableCell>
                  <TableCell>
                    {isEditing ? (
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-1.5">
                          <span className="text-muted-foreground text-xs">$</span>
                          <Input
                            type="number"
                            step="any"
                            min="0"
                            className="h-7 w-28 text-xs"
                            value={editValue}
                            data-testid={`budget-input-${testKey}`}
                            onChange={(e) => {
                              setEditValue(e.target.value);
                              setValidationError(null);
                            }}
                            autoFocus
                            onKeyDown={(e) => {
                              if (e.key === "Enter") void saveBudget(b);
                              if (e.key === "Escape") cancelEditing();
                            }}
                          />
                          <Button
                            size="icon-xs"
                            variant="default"
                            onClick={() => void saveBudget(b)}
                            aria-label="Save budget cap"
                            data-testid={`save-budget-btn-${testKey}`}
                          >
                            <Check className="size-3" />
                          </Button>
                          <Button size="icon-xs" variant="ghost" onClick={cancelEditing} aria-label="Cancel editing">
                            <X className="size-3" />
                          </Button>
                        </div>
                        {validationError ? (
                          <span className="text-[11px] text-destructive" data-testid={`budget-error-${testKey}`}>
                            {validationError}
                          </span>
                        ) : null}
                      </div>
                    ) : (
                      <div className="group flex items-center gap-2">
                        <span className="font-mono text-sm tabular-nums">{fmtUsd(b.monthly_usd, { whole: true })}</span>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          className="text-muted-foreground/70 transition-colors hover:text-foreground"
                          onClick={() => startEditing(b)}
                          aria-label={`Edit ${scopeLabel} budget`}
                          data-testid={`edit-budget-btn-${testKey}`}
                        >
                          <Edit2 className="size-3" />
                        </Button>
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-mono tabular-nums">{fmtUsd(spent)}</span>
                        <span className="text-[11px] text-muted-foreground">{fmtPct(ratio)}</span>
                      </div>
                      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-muted">
                        <div
                          className="h-full rounded-full transition-all"
                          style={{
                            width: `${Math.min(ratio * 100, 100)}%`,
                            backgroundColor: ratio >= 1 ? "var(--color-destructive)" : "var(--primary)",
                          }}
                        />
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5 text-xs">
                      <span className="font-mono tabular-nums">{fmtUsd(forecast)}</span>
                      {isOver ? (
                        <ToneBadge tone="warn" title="Forecast exceeds cap">
                          Over
                        </ToneBadge>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="inline-flex items-center gap-2">
                      <Switch
                        checked={b.hard_stop}
                        onCheckedChange={(checked) => toggleHardStop(b, checked)}
                        aria-label={`Toggle hard stop for ${scopeLabel}`}
                      />
                      {b.hard_stop ? (
                        <span title="Hard stop active">
                          <ShieldAlert className="size-3.5 text-muted-foreground" />
                        </span>
                      ) : null}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
