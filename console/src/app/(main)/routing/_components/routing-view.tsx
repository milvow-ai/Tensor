"use client";

import { useState } from "react";

import { ArrowDown, ArrowUp, Check, Clock, GripVertical, ShieldAlert, Sparkles, Zap } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { submitCommand } from "@/lib/farm/actions";
import { fmtInt } from "@/lib/farm/format";
import { POOL_STRATEGIES, type PoolStrategy, type RouteRow } from "@/lib/farm/types";

interface AccountPreview {
  id: string;
  label: string;
  status: string;
  remaining: number | null;
  unit: string | null;
  latencyMs: number | null;
  circuit: string;
}

export function RoutingView({
  initialRoutes,
  accountsByPool,
}: {
  initialRoutes: RouteRow[];
  accountsByPool: Record<string, AccountPreview[]>;
}) {
  // Group routes by capability
  const capabilities = Array.from(new Set(initialRoutes.map((r) => r.capability)));
  const [selectedCap, setSelectedCap] = useState<string>(capabilities[0] ?? "");
  const [routesByCap, setRoutesByCap] = useState<Record<string, RouteRow[]>>(() => {
    const map: Record<string, RouteRow[]> = {};
    for (const cap of capabilities) {
      map[cap] = initialRoutes.filter((r) => r.capability === cap).sort((a, b) => a.position - b.position);
    }
    return map;
  });

  const [strategyByCap, setStrategyByCap] = useState<Record<string, PoolStrategy>>(() => {
    const map: Record<string, PoolStrategy> = {};
    for (const r of initialRoutes) {
      if (!map[r.capability]) {
        map[r.capability] = (r.default_strategy as PoolStrategy) || "failover";
      }
    }
    return map;
  });

  const [cacheTtlByCap, setCacheTtlByCap] = useState<Record<string, number>>(() => {
    const map: Record<string, number> = {};
    for (const r of initialRoutes) {
      if (map[r.capability] === undefined) {
        map[r.capability] = r.cache_ttl_seconds;
      }
    }
    return map;
  });

  const [saving, setSaving] = useState(false);
  const [draggedIdx, setDraggedIdx] = useState<number | null>(null);

  const currentRoutes = routesByCap[selectedCap] ?? [];
  const currentStrategy = strategyByCap[selectedCap] ?? "failover";
  const currentTtl = cacheTtlByCap[selectedCap] ?? 3600;

  // Compute live "who would answer now"
  function computeWhoWouldAnswer(routes: RouteRow[]) {
    for (const route of routes) {
      if (!route.enabled) continue;
      const accs = accountsByPool[route.provider_id] ?? [];
      const eligible = accs.find((a) => a.status === "active" && a.circuit !== "open");
      if (eligible) {
        return {
          poolName: route.provider_name,
          accountLabel: eligible.label,
          accountId: eligible.id,
          reason:
            eligible.remaining !== null
              ? `${fmtInt(eligible.remaining)} ${eligible.unit ?? "units"} remaining`
              : "Active unlimited account",
          latencyMs: eligible.latencyMs,
          isFailover: route.position > 1,
        };
      }
    }
    return null;
  }

  const livePreview = computeWhoWouldAnswer(currentRoutes);

  // Move pool up/down
  function movePool(index: number, direction: "up" | "down") {
    const newIdx = direction === "up" ? index - 1 : index + 1;
    if (newIdx < 0 || newIdx >= currentRoutes.length) return;

    const updated = [...currentRoutes];
    const [moved] = updated.splice(index, 1);
    if (!moved) return;
    updated.splice(newIdx, 0, moved);

    // Re-index positions
    const reindexed = updated.map((r, idx) => ({ ...r, position: idx + 1 }));
    setRoutesByCap((prev) => ({ ...prev, [selectedCap]: reindexed }));
  }

  // Toggle pool enabled
  function togglePoolEnabled(providerId: string, enabled: boolean) {
    const updated = currentRoutes.map((r) => (r.provider_id === providerId ? { ...r, enabled } : r));
    setRoutesByCap((prev) => ({ ...prev, [selectedCap]: updated }));
  }

  // Drag and drop handlers
  function handleDragStart(idx: number) {
    setDraggedIdx(idx);
  }

  function handleDragOver(e: React.DragEvent, idx: number) {
    e.preventDefault();
    if (draggedIdx === null || draggedIdx === idx) return;

    const updated = [...currentRoutes];
    const [moved] = updated.splice(draggedIdx, 1);
    if (!moved) return;
    updated.splice(idx, 0, moved);

    const reindexed = updated.map((r, i) => ({ ...r, position: i + 1 }));
    setRoutesByCap((prev) => ({ ...prev, [selectedCap]: reindexed }));
    setDraggedIdx(idx);
  }

  function handleDragEnd() {
    setDraggedIdx(null);
  }

  // Save changes via commands
  async function saveRouteChanges() {
    setSaving(true);
    try {
      // 1. Enqueue set_strategy if changed
      await submitCommand("set_strategy", {
        capability: selectedCap,
        strategy: currentStrategy,
      });

      // 2. Enqueue set_route for each pool in order
      for (const r of currentRoutes) {
        await submitCommand("set_route", {
          capability: selectedCap,
          provider_id: r.provider_id,
          position: r.position,
          enabled: r.enabled,
        });
      }

      toast.success(`Routing for "${selectedCap}" updated.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save route changes.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Capability selector tabs */}
      <Tabs value={selectedCap} onValueChange={setSelectedCap} className="w-full">
        <TabsList className="h-auto flex-wrap gap-1 bg-muted/60 p-1">
          {capabilities.map((cap) => (
            <TabsTrigger
              key={cap}
              value={cap}
              className="px-3 py-1.5 font-mono text-xs data-[state=active]:bg-background data-[state=active]:shadow-xs"
            >
              {cap}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Main configuration grid */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Ordered Pools (Left 2 cols) */}
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="font-semibold text-base">Route Order: {selectedCap}</CardTitle>
                  <CardDescription className="text-xs">
                    Drag or use buttons to reorder pools. Calls try pools in this exact order.
                  </CardDescription>
                </div>
                <Button size="sm" onClick={saveRouteChanges} disabled={saving} className="gap-1.5">
                  <Check className="size-3.5" />
                  <span>{saving ? "Saving..." : "Save Route"}</span>
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              {currentRoutes.map((route, idx) => {
                const accounts = accountsByPool[route.provider_id] ?? [];
                const activeCount = accounts.filter((a) => a.status === "active" && a.circuit !== "open").length;

                return (
                  // biome-ignore lint/a11y/noStaticElementInteractions: Drag reorder has keyboard button alternatives
                  <div
                    key={route.provider_id}
                    draggable
                    onDragStart={() => handleDragStart(idx)}
                    onDragOver={(e) => handleDragOver(e, idx)}
                    onDragEnd={handleDragEnd}
                    className={`flex items-center justify-between rounded-lg border p-3 transition-colors ${
                      route.enabled ? "bg-card hover:border-primary/40" : "bg-muted/40 opacity-60"
                    } ${draggedIdx === idx ? "border-primary shadow-md" : ""}`}
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className="cursor-grab text-muted-foreground hover:text-foreground active:cursor-grabbing"
                        title="Drag to reorder"
                      >
                        <GripVertical className="size-4" />
                      </div>
                      <Badge
                        variant="outline"
                        className="flex size-6 items-center justify-center p-0 font-mono text-xs"
                      >
                        {route.position}
                      </Badge>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm">{route.provider_name}</span>
                          <Badge
                            variant={route.health === "healthy" ? "outline" : "destructive"}
                            className="text-[10px] capitalize"
                          >
                            {route.health}
                          </Badge>
                        </div>
                        <p className="text-muted-foreground text-xs">
                          {activeCount} of {accounts.length} accounts usable •{" "}
                          {route.unlimited ? "Unlimited" : `${fmtInt(route.remaining_calls)} calls`} remaining
                        </p>
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        disabled={idx === 0}
                        onClick={() => movePool(idx, "up")}
                        title="Move up"
                        aria-label={`Move ${route.provider_name} up`}
                      >
                        <ArrowUp className="size-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        disabled={idx === currentRoutes.length - 1}
                        onClick={() => movePool(idx, "down")}
                        title="Move down"
                        aria-label={`Move ${route.provider_name} down`}
                      >
                        <ArrowDown className="size-3.5" />
                      </Button>
                      <div className="mx-1 h-4 w-px bg-border" />
                      <div className="flex items-center gap-1.5">
                        <Label htmlFor={`enable-${route.provider_id}`} className="text-muted-foreground text-xs">
                          {route.enabled ? "Enabled" : "Disabled"}
                        </Label>
                        <Switch
                          id={`enable-${route.provider_id}`}
                          checked={route.enabled}
                          onCheckedChange={(checked) => togglePoolEnabled(route.provider_id, checked)}
                          aria-label={`Enable pool ${route.provider_name}`}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>

        {/* Live Preview & Settings (Right col) */}
        <div className="space-y-4">
          {/* Live Preview Card */}
          <Card className="border-primary/30 bg-primary/5">
            <CardHeader className="pb-2">
              <div className="flex items-center gap-2">
                <Sparkles className="size-4 text-primary" />
                <CardTitle className="font-semibold text-sm">Who Would Answer Now</CardTitle>
              </div>
              <CardDescription className="text-xs">
                Real-time routing simulation based on current health, circuit breakers, and balances.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 pt-1">
              {livePreview ? (
                <div className="space-y-2 rounded-md border bg-background/80 p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-foreground text-xs">{livePreview.poolName}</span>
                    <Badge variant={livePreview.isFailover ? "secondary" : "default"} className="text-[10px]">
                      {livePreview.isFailover ? "Failover Target" : "Primary"}
                    </Badge>
                  </div>
                  <div className="space-y-1">
                    <div className="font-medium font-mono text-primary text-xs">{livePreview.accountLabel}</div>
                    <div className="text-[11px] text-muted-foreground">{livePreview.reason}</div>
                  </div>
                  {livePreview.latencyMs !== null && (
                    <div className="flex items-center gap-1 border-t pt-1 text-[11px] text-muted-foreground">
                      <Zap className="size-3 text-amber-500" />
                      <span>p50 latency: ~{livePreview.latencyMs} ms</span>
                    </div>
                  )}
                </div>
              ) : (
                <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-destructive text-xs">
                  <ShieldAlert className="size-4 shrink-0" />
                  <span>All pools in this route are currently degraded or disabled!</span>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Strategy & TTL Controls */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="font-semibold text-sm">Capability Defaults</CardTitle>
              <CardDescription className="text-xs">Strategy and caching defaults for this capability.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="strategy-select" className="font-medium text-xs">
                  Default Strategy
                </Label>
                <Select
                  value={currentStrategy}
                  onValueChange={(val) => setStrategyByCap((prev) => ({ ...prev, [selectedCap]: val as PoolStrategy }))}
                >
                  <SelectTrigger id="strategy-select" className="w-full font-mono text-xs">
                    <SelectValue placeholder="Select strategy" />
                  </SelectTrigger>
                  <SelectContent>
                    {POOL_STRATEGIES.map((strat) => (
                      <SelectItem key={strat} value={strat} className="font-mono text-xs">
                        {strat}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-[11px] text-muted-foreground">
                  How candidate pools and accounts are selected when callers do not specify a strategy.
                </p>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="cache-ttl-input" className="font-medium text-xs">
                  Cache TTL (seconds)
                </Label>
                <div className="relative">
                  <Clock className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
                  <Input
                    id="cache-ttl-input"
                    type="number"
                    min="0"
                    max="86400"
                    value={currentTtl}
                    onChange={(e) =>
                      setCacheTtlByCap((prev) => ({ ...prev, [selectedCap]: Number.parseInt(e.target.value, 10) || 0 }))
                    }
                    className="pl-8 font-mono text-xs"
                  />
                </div>
                <p className="text-[11px] text-muted-foreground">
                  Identical request payloads within this time are served from cache at zero cost.
                </p>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
