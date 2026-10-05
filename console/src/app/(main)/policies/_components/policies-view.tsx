"use client";

import { useState } from "react";

import { Bell, Check, Edit2, History, Lock, Search, Shield, ShieldCheck, UserCheck } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { submitCommand } from "@/lib/farm/actions";
import { fmtAbsolute, fmtInt } from "@/lib/farm/format";
import type { AuditEventRow, BudgetRow, SpendMonthRow } from "@/lib/farm/types";

function getBudgetBarColor(pct: number): string {
  if (pct >= 90) return "bg-destructive";
  if (pct >= 75) return "bg-amber-500";
  return "bg-primary";
}

export function PoliciesView({
  initialBudgets,
  spendMonth,
  initialAuditEvents,
}: {
  initialBudgets: BudgetRow[];
  spendMonth: SpendMonthRow[];
  initialAuditEvents: AuditEventRow[];
}) {
  const [budgets, setBudgets] = useState<BudgetRow[]>(initialBudgets);
  const [auditEvents, _setAuditEvents] = useState<AuditEventRow[]>(initialAuditEvents);

  // Global budget state
  const globalBudget = budgets.find((b) => b.scope === "global");
  const [globalCap, setGlobalCap] = useState(globalBudget?.monthly_usd ?? 7000);
  const [globalHardStop, setGlobalHardStop] = useState(globalBudget?.hard_stop ?? true);
  const [editingGlobal, setEditingGlobal] = useState(false);
  const [savingGlobal, setSavingGlobal] = useState(false);

  // Per-provider budget editing state
  const [editingProvider, setEditingProvider] = useState<string | null>(null);
  const [providerCapInput, setProviderCapInput] = useState<number>(0);
  const [savingProvider, setSavingProvider] = useState(false);

  // Audit filter state
  const [auditSearch, setAuditSearch] = useState("");
  const [auditActionFilter, setAuditActionFilter] = useState("all");
  const [inspectedAudit, setInspectedAudit] = useState<AuditEventRow | null>(null);

  // Global spend total
  const totalSpendRow = spendMonth.find((s) => s.provider_id === "total");
  const currentTotalSpend = totalSpendRow?.spend_usd ?? 0;
  const globalPct = globalCap > 0 ? Math.min(100, Math.round((currentTotalSpend / globalCap) * 100)) : 0;

  // Save global budget
  async function saveGlobalBudget() {
    setSavingGlobal(true);
    try {
      await submitCommand("set_budget", {
        scope: "global",
        monthly_usd: globalCap,
        hard_stop: globalHardStop,
      });
      setBudgets((prev) =>
        prev.map((b) => (b.scope === "global" ? { ...b, monthly_usd: globalCap, hard_stop: globalHardStop } : b)),
      );
      setEditingGlobal(false);
      toast.success("Global Farm spending budget updated.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update global budget.");
    } finally {
      setSavingGlobal(false);
    }
  }

  // Toggle global hard stop immediately
  async function toggleGlobalHardStop(checked: boolean) {
    setGlobalHardStop(checked);
    try {
      await submitCommand("set_budget", {
        scope: "global",
        monthly_usd: globalCap,
        hard_stop: checked,
      });
      setBudgets((prev) => prev.map((b) => (b.scope === "global" ? { ...b, hard_stop: checked } : b)));
      toast.success(`Global hard stop ${checked ? "enabled" : "disabled"}.`);
    } catch {
      setGlobalHardStop(!checked);
      toast.error("Could not update hard stop setting.");
    }
  }

  // Save provider budget
  async function saveProviderBudget(providerId: string, hardStop: boolean) {
    setSavingProvider(true);
    try {
      await submitCommand("set_budget", {
        scope: "provider",
        ref: providerId,
        monthly_usd: providerCapInput,
        hard_stop: hardStop,
      });
      setBudgets((prev) => {
        const exists = prev.some((b) => b.scope === "provider" && b.ref === providerId);
        if (exists) {
          return prev.map((b) =>
            b.scope === "provider" && b.ref === providerId
              ? { ...b, monthly_usd: providerCapInput, hard_stop: hardStop }
              : b,
          );
        }
        return [
          ...prev,
          {
            id: `b_${providerId}`,
            scope: "provider",
            ref: providerId,
            monthly_usd: providerCapInput,
            hard_stop: hardStop,
          },
        ];
      });
      setEditingProvider(null);
      toast.success(`Budget for "${providerId}" set to $${providerCapInput.toLocaleString()}.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to set provider budget.");
    } finally {
      setSavingProvider(false);
    }
  }

  // Toggle provider hard stop
  async function toggleProviderHardStop(b: BudgetRow, checked: boolean) {
    try {
      await submitCommand("set_budget", {
        scope: "provider",
        ref: b.ref,
        monthly_usd: b.monthly_usd,
        hard_stop: checked,
      });
      setBudgets((prev) => prev.map((item) => (item.id === b.id ? { ...item, hard_stop: checked } : item)));
      toast.success(`Hard stop for ${b.ref} ${checked ? "enabled" : "disabled"}.`);
    } catch {
      toast.error("Could not update provider hard stop.");
    }
  }

  // Filter audit events
  const filteredAudits = auditEvents.filter((ae) => {
    if (auditActionFilter !== "all" && ae.action !== auditActionFilter) return false;
    if (auditSearch) {
      const q = auditSearch.toLowerCase();
      return (
        ae.actor.toLowerCase().includes(q) || ae.action.toLowerCase().includes(q) || ae.target.toLowerCase().includes(q)
      );
    }
    return true;
  });

  return (
    <div className="space-y-6">
      {/* Top row: Global Budget & Alert Thresholds */}
      <div className="grid gap-6 md:grid-cols-3">
        {/* Global Budget Card */}
        <Card className="md:col-span-2">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-5 text-primary" />
                <div>
                  <CardTitle className="font-semibold text-base">Global Spending Cap</CardTitle>
                  <CardDescription className="text-xs">
                    Monthly spending ceiling across all providers and AI workers.
                  </CardDescription>
                </div>
              </div>

              {!editingGlobal ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1 text-xs"
                  onClick={() => {
                    setGlobalCap(globalBudget?.monthly_usd ?? 7000);
                    setEditingGlobal(true);
                  }}
                >
                  <Edit2 className="size-3" />
                  <span>Edit Cap</span>
                </Button>
              ) : (
                <div className="flex items-center gap-2">
                  <Button variant="ghost" size="sm" className="h-8 text-xs" onClick={() => setEditingGlobal(false)}>
                    Cancel
                  </Button>
                  <Button size="sm" className="h-8 gap-1 text-xs" onClick={saveGlobalBudget} disabled={savingGlobal}>
                    <Check className="size-3" />
                    <span>Save</span>
                  </Button>
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-baseline">
              <div className="flex items-baseline gap-2">
                {editingGlobal ? (
                  <div className="flex items-center gap-1">
                    <span className="font-bold text-lg">$</span>
                    <Input
                      type="number"
                      min="0"
                      max="100000"
                      value={globalCap}
                      onChange={(e) => setGlobalCap(Number(e.target.value) || 0)}
                      className="h-8 w-32 font-mono font-semibold text-sm"
                    />
                  </div>
                ) : (
                  <span className="font-bold text-2xl tracking-tight">${fmtInt(globalCap)}</span>
                )}
                <span className="text-muted-foreground text-xs">/ month</span>
              </div>

              <div className="text-muted-foreground text-xs">
                Current month spend: <strong className="text-foreground">${currentTotalSpend.toFixed(2)}</strong> (
                {globalPct}%)
              </div>
            </div>

            {/* Progress Bar */}
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div
                className={`h-full transition-all ${getBudgetBarColor(globalPct)}`}
                style={{ width: `${globalPct}%` }}
              />
            </div>

            <div className="flex items-center justify-between border-t pt-1 text-xs">
              <div className="flex items-center gap-2">
                <Lock className="size-3.5 text-muted-foreground" />
                <Label htmlFor="global-hard-stop" className="cursor-pointer font-medium text-xs">
                  Hard Stop at 100%
                </Label>
              </div>
              <Switch
                id="global-hard-stop"
                checked={globalHardStop}
                onCheckedChange={toggleGlobalHardStop}
                aria-label="Toggle global budget hard stop"
              />
            </div>
          </CardContent>
        </Card>

        {/* Alert Thresholds Card */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center gap-2">
              <Bell className="size-4 text-primary" />
              <CardTitle className="font-semibold text-sm">Alert Thresholds</CardTitle>
            </div>
            <CardDescription className="text-xs">
              Automatic warnings raised as month-end forecasts approach budget limits.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between rounded border bg-muted/20 p-2">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="font-mono font-semibold text-xs">
                  50%
                </Badge>
                <span className="text-muted-foreground text-xs">Pacing advisory</span>
              </div>
              <Badge variant="secondary" className="text-[10px]">
                Info
              </Badge>
            </div>

            <div className="flex items-center justify-between rounded border border-amber-500/20 bg-amber-500/10 p-2">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="border-amber-500/40 font-mono font-semibold text-amber-600 text-xs">
                  80%
                </Badge>
                <span className="font-medium text-amber-700 text-xs dark:text-amber-400">Budget warning</span>
              </div>
              <Badge className="border-0 bg-amber-500/20 text-[10px] text-amber-700 dark:text-amber-400">Warn</Badge>
            </div>

            <div className="flex items-center justify-between rounded border border-destructive/20 bg-destructive/10 p-2">
              <div className="flex items-center gap-2">
                <Badge variant="destructive" className="font-mono font-semibold text-xs">
                  100%
                </Badge>
                <span className="font-medium text-destructive text-xs">Hard stop trigger</span>
              </div>
              <Badge variant="destructive" className="text-[10px]">
                Critical
              </Badge>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Per-Provider Budgets Table */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="font-semibold text-base">Provider Spending Caps</CardTitle>
          <CardDescription className="text-xs">
            Individual caps per provider pool. If hard stop is enabled, calls fail over to alternate pools.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[180px]">Provider Pool</TableHead>
                <TableHead className="w-[100px]">Kind</TableHead>
                <TableHead className="w-[160px]">Monthly Budget</TableHead>
                <TableHead className="w-[140px]">Month Spend</TableHead>
                <TableHead className="w-[120px] text-right">Hard Stop</TableHead>
                <TableHead className="w-[100px] text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {spendMonth
                .filter((s) => s.provider_id !== "total")
                .map((row) => {
                  const b = budgets.find((item) => item.scope === "provider" && item.ref === row.provider_id);
                  const cap = b?.monthly_usd ?? 0;
                  const hardStop = b?.hard_stop ?? true;
                  const isEditing = editingProvider === row.provider_id;

                  return (
                    <TableRow key={row.provider_id}>
                      <TableCell className="font-medium text-xs">{row.provider_name}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className="font-mono text-[10px] uppercase">
                          {row.kind}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {isEditing ? (
                          <div className="flex items-center gap-1">
                            <span className="font-mono text-xs">$</span>
                            <Input
                              type="number"
                              min="0"
                              max="10000"
                              value={providerCapInput}
                              onChange={(e) => setProviderCapInput(Number(e.target.value) || 0)}
                              className="h-7 w-24 font-mono text-xs"
                            />
                          </div>
                        ) : (
                          <span className="font-mono font-semibold text-xs">
                            ${cap > 0 ? fmtInt(cap) : "None"}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-muted-foreground text-xs">
                        ${row.spend_usd.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right">
                        <Switch
                          checked={hardStop}
                          disabled={!b}
                          onCheckedChange={(checked) => b && toggleProviderHardStop(b, checked)}
                          aria-label={`Toggle hard stop for ${row.provider_name}`}
                        />
                      </TableCell>
                      <TableCell className="text-right">
                        {isEditing ? (
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-6 px-1.5 text-xs"
                              onClick={() => setEditingProvider(null)}
                            >
                              Cancel
                            </Button>
                            <Button
                              size="sm"
                              className="h-6 px-2 text-xs"
                              onClick={() => saveProviderBudget(row.provider_id, hardStop)}
                              disabled={savingProvider}
                            >
                              Save
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 text-xs"
                            onClick={() => {
                              setProviderCapInput(cap);
                              setEditingProvider(row.provider_id);
                            }}
                          >
                            Edit
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Per-Caller Rules Card */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2">
            <UserCheck className="size-4 text-primary" />
            <CardTitle className="font-semibold text-base">Caller Permissions & Security Boundaries</CardTitle>
          </div>
          <CardDescription className="text-xs">
            What autonomous callers and external agents are authorized to execute through the Farm gateway.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-xs">Hermes Agent</span>
                <Badge variant="secondary" className="text-[10px]">
                  executor: agent
                </Badge>
              </div>
              <p className="text-muted-foreground text-xs">
                Strict confinement: Farm tools only. Terminal and web keyless fallbacks disabled; sampling disabled;
                virtual budget via Bifrost.
              </p>
            </div>
            <Badge className="border-emerald-500/30 bg-emerald-500/15 text-[10px] text-emerald-700 dark:text-emerald-400">
              Active Profile
            </Badge>
          </div>

          <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-xs">Claude Code & Codex CLIs</span>
                <Badge variant="outline" className="text-[10px]">
                  executor: cli_agent
                </Badge>
              </div>
              <p className="text-muted-foreground text-xs">
                Authorized for multi-turn iterative jobs. Edit mode confined to user-specified repository directories.
              </p>
            </div>
            <Badge variant="outline" className="text-[10px]">
              Concurred
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* Audit Log Table */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
            <div>
              <div className="flex items-center gap-2">
                <History className="size-4 text-primary" />
                <CardTitle className="font-semibold text-base">Control Audit Log</CardTitle>
              </div>
              <CardDescription className="text-xs">
                Immutable audit trail of configuration changes, budget updates, and route overrides.
              </CardDescription>
            </div>

            <div className="flex items-center gap-2">
              <Select value={auditActionFilter} onValueChange={setAuditActionFilter}>
                <SelectTrigger className="h-8 w-[140px] font-mono text-xs">
                  <SelectValue placeholder="Action" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all" className="text-xs">
                    All Actions
                  </SelectItem>
                  <SelectItem value="set_budget" className="font-mono text-xs">
                    set_budget
                  </SelectItem>
                  <SelectItem value="set_route" className="font-mono text-xs">
                    set_route
                  </SelectItem>
                  <SelectItem value="add_connection" className="font-mono text-xs">
                    add_connection
                  </SelectItem>
                  <SelectItem value="pause_account" className="font-mono text-xs">
                    pause_account
                  </SelectItem>
                  <SelectItem value="update_strategy" className="font-mono text-xs">
                    update_strategy
                  </SelectItem>
                </SelectContent>
              </Select>

              <div className="relative w-48 sm:w-60">
                <Search className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
                <Input
                  placeholder="Filter actor or target..."
                  value={auditSearch}
                  onChange={(e) => setAuditSearch(e.target.value)}
                  className="h-8 pl-8 font-mono text-xs"
                />
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[140px]">Timestamp</TableHead>
                <TableHead className="w-[180px]">Actor</TableHead>
                <TableHead className="w-[140px]">Action</TableHead>
                <TableHead>Target</TableHead>
                <TableHead className="w-[90px] text-right">Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredAudits.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-24 text-center text-muted-foreground text-xs">
                    No audit events match current criteria.
                  </TableCell>
                </TableRow>
              ) : (
                filteredAudits.map((ae) => (
                  <TableRow key={ae.id}>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                      {fmtAbsolute(ae.at)}
                    </TableCell>
                    <TableCell className="max-w-[180px] truncate font-medium text-xs">{ae.actor}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {ae.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="max-w-[200px] truncate font-mono text-xs">{ae.target}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => setInspectedAudit(ae)}>
                        Inspect
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Audit Detail Modal */}
      <Dialog open={inspectedAudit !== null} onOpenChange={(open) => !open && setInspectedAudit(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Shield className="size-4 text-primary" />
              <span>Audit Event #{inspectedAudit?.id}</span>
            </DialogTitle>
            <DialogDescription className="text-xs">
              {inspectedAudit?.action} performed on {inspectedAudit?.target} by {inspectedAudit?.actor}
            </DialogDescription>
          </DialogHeader>

          {inspectedAudit && (
            <div className="space-y-3 pt-2 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <span className="font-semibold text-muted-foreground">State Before:</span>
                  <div className="max-h-48 overflow-auto rounded border bg-muted/60 p-2 font-mono text-[11px]">
                    <pre>{JSON.stringify(inspectedAudit.before, null, 2)}</pre>
                  </div>
                </div>

                <div className="space-y-1">
                  <span className="font-semibold text-muted-foreground">State After:</span>
                  <div className="max-h-48 overflow-auto rounded border bg-muted/60 p-2 font-mono text-[11px]">
                    <pre>{JSON.stringify(inspectedAudit.after, null, 2)}</pre>
                  </div>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
