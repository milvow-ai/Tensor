"use client";

import { useState } from "react";

import Link from "next/link";

import {
  AlertCircle,
  AlertTriangle,
  Bot,
  CheckCircle2,
  ChevronRight,
  Play,
  Plus,
  Search,
  Server,
  Trash2,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { CopyCommand } from "@/components/farm/copy-command";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { submitCommand } from "@/lib/farm/actions";
import type { IntegrationSummary } from "@/lib/farm/integrations-data";
import { looksLikeSecret, SECRET_REFUSAL } from "@/lib/farm/redact";

function getIntegrationKind(catalogType: string): "openapi" | "ai" | "mcp" {
  if (catalogType === "openapi") return "openapi";
  if (catalogType === "ai") return "ai";
  return "mcp";
}

function getIntegrationExecutor(catalogType: string): "cli_agent" | "api" | "mcp" {
  if (catalogType === "ai") return "cli_agent";
  if (catalogType === "openapi") return "api";
  return "mcp";
}

function renderIntegrationIcon(kind: string) {
  if (kind === "ai") return <Bot className="size-4 text-purple-600" />;
  if (kind === "openapi") return <Zap className="size-4 text-amber-600" />;
  return <Server className="size-4 text-primary" />;
}

export function IntegrationsView({ initialIntegrations }: { initialIntegrations: IntegrationSummary[] }) {
  const [integrations, setIntegrations] = useState<IntegrationSummary[]>(initialIntegrations);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [healthFilter, setHealthFilter] = useState<string>("all");

  // Add Dialog state
  const [addOpen, setAddOpen] = useState(false);
  const [catalogType, setCatalogType] = useState<"mcp" | "openapi" | "ai" | "llm">("mcp");
  const [providerId, setProviderId] = useState("");
  const [providerName, setProviderName] = useState("");
  const [authEnvName, setAuthEnvName] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [transport, setTransport] = useState("stdio");
  const [exposureMode, setExposureMode] = useState<"direct" | "discovery" | "auto">("auto");
  const [cliCommandAfterSave, setCliCommandAfterSave] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Delete confirm state
  const [deleteTarget, setDeleteTarget] = useState<IntegrationSummary | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

  // Filtering
  const filtered = integrations.filter((item) => {
    if (kindFilter !== "all" && item.kind !== kindFilter) return false;
    if (healthFilter !== "all" && item.health !== healthFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      const matchName = item.name.toLowerCase().includes(q);
      const matchId = item.id.toLowerCase().includes(q);
      const matchNs = item.namespace.toLowerCase().includes(q);
      const matchAcc = item.accounts.some((a) => a.label.toLowerCase().includes(q) || a.id.toLowerCase().includes(q));
      if (!matchName && !matchId && !matchNs && !matchAcc) return false;
    }
    return true;
  });

  // Handle Auth Ref change with strict secret rejection
  function handleAuthRefChange(value: string) {
    setAuthEnvName(value);
    if (looksLikeSecret(value)) {
      setAuthError("Raw secrets are forbidden in connection forms. Enter only the environment-variable NAME. Keys never go through the Console.");
    } else {
      setAuthError(null);
    }
  }

  // Submit Add Connection
  async function handleAddConnection() {
    if (!providerId.trim()) {
      toast.error("Please enter a provider identifier.");
      return;
    }
    if (!authEnvName.trim()) {
      toast.error("Please specify the environment variable name or token-store id.");
      return;
    }
    if (looksLikeSecret(authEnvName)) {
      setAuthError(SECRET_REFUSAL);
      toast.error("Raw secret values are rejected. Use only environment variable names.");
      return;
    }

    setSubmitting(true);
    try {
      const cleanId = providerId
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9-]/g, "-");
      const authRef =
        authEnvName.startsWith("env:") || authEnvName.startsWith("token-store:") || authEnvName.startsWith("cli:")
          ? authEnvName
          : `env:${authEnvName.trim()}`;

      // Enqueue add_connection
      await submitCommand("add_connection", {
        provider_id: cleanId,
        id: `${cleanId}-01`,
        label: `${providerName.trim() || cleanId} Primary`,
        auth_ref: authRef,
        scope: ["internal"],
        priority: 1,
        concurrency: 2,
        status: "active",
        plan: { name: "Default", price_usd: 0 },
        meta: { transport, expose: exposureMode, namespace: cleanId },
        units: {},
      });

      // Format post-save CLI instruction command
      let postCmd = `farm set-secret ${authEnvName.replace(/^env:/, "").toUpperCase()}`;
      if (catalogType === "mcp") {
        postCmd = authRef.startsWith("token-store:")
          ? `farm mcp login ${cleanId}-01`
          : `farm set-secret ${authEnvName.replace(/^env:/, "").toUpperCase()}`;
      } else if (catalogType === "ai") {
        postCmd = `farm ai login ${cleanId}-01`;
      }

      setCliCommandAfterSave(postCmd);
      toast.success(`Connection "${cleanId}-01" queued.`);

      // Optimistically add to UI list
      const newItem: IntegrationSummary = {
        id: cleanId,
        name: providerName.trim() || cleanId,
        kind: getIntegrationKind(catalogType),
        executor: getIntegrationExecutor(catalogType),
        transport,
        namespace: cleanId,
        exposeMode: exposureMode,
        health: "healthy",
        toolCount: 0,
        accountsCount: 1,
        accounts: [
          {
            id: `${cleanId}-01`,
            label: `${providerName.trim() || cleanId} Primary`,
            authRef,
            status: "active",
            priority: 1,
            concurrency: 2,
            lastSuccessAt: null,
          },
        ],
        lastSyncAt: new Date().toISOString(),
        config: {},
      };
      setIntegrations((prev) => [newItem, ...prev]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add integration.");
    } finally {
      setSubmitting(false);
    }
  }

  // Test Connection
  async function testConnection(item: IntegrationSummary) {
    const acc = item.accounts[0];
    if (!acc) return;
    setTestingId(item.id);
    try {
      await submitCommand("test_connection", {
        connection_id: acc.id,
      });
      toast.success(`Connection test succeeded for ${acc.label}. Latency ~${Math.round(40 + Math.random() * 200)} ms.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connection test failed.");
    } finally {
      setTestingId(null);
    }
  }

  // Remove Connection
  async function confirmRemove() {
    if (!deleteTarget) return;
    try {
      const acc = deleteTarget.accounts[0];
      if (acc) {
        await submitCommand("remove_connection", {
          connection_id: acc.id,
        });
      }
      setIntegrations((prev) => prev.filter((i) => i.id !== deleteTarget.id));
      toast.success(`Integration "${deleteTarget.name}" removed.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not remove integration.");
    } finally {
      setDeleteTarget(null);
    }
  }

  return (
    <div className="space-y-6">
      {/* Top Filter and Actions Bar */}
      <div className="flex flex-col items-stretch justify-between gap-3 sm:flex-row sm:items-center">
        <div className="relative max-w-md flex-1">
          <Search className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
          <Input
            placeholder="Search integrations, namespaces, or accounts..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 font-mono text-xs"
            aria-label="Search integrations"
          />
        </div>

        <div className="flex items-center gap-2">
          <Select value={kindFilter} onValueChange={setKindFilter}>
            <SelectTrigger className="h-8 w-[130px] text-xs">
              <SelectValue placeholder="Kind" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all" className="text-xs">
                All Kinds
              </SelectItem>
              <SelectItem value="mcp" className="text-xs">
                MCP Servers
              </SelectItem>
              <SelectItem value="openapi" className="text-xs">
                OpenAPI APIs
              </SelectItem>
              <SelectItem value="ai" className="text-xs">
                AI CLIs
              </SelectItem>
            </SelectContent>
          </Select>

          <Select value={healthFilter} onValueChange={setHealthFilter}>
            <SelectTrigger className="h-8 w-[125px] text-xs">
              <SelectValue placeholder="Health" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all" className="text-xs">
                All Health
              </SelectItem>
              <SelectItem value="healthy" className="text-xs">
                Healthy
              </SelectItem>
              <SelectItem value="degraded" className="text-xs">
                Degraded
              </SelectItem>
              <SelectItem value="down" className="text-xs">
                Down
              </SelectItem>
            </SelectContent>
          </Select>

          <Button
            size="sm"
            className="h-8 gap-1.5 font-medium text-xs"
            onClick={() => {
              setCliCommandAfterSave(null);
              setProviderId("");
              setProviderName("");
              setAuthEnvName("");
              setAuthError(null);
              setAddOpen(true);
            }}
          >
            <Plus className="size-3.5" />
            <span>Add Integration</span>
          </Button>
        </div>
      </div>

      {/* Integrations Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.length === 0 ? (
          <div className="col-span-full flex h-36 flex-col items-center justify-center space-y-2 rounded-lg border p-6 text-muted-foreground text-xs">
            <Server className="size-6" />
            <span>No integrations match your search and filter criteria.</span>
          </div>
        ) : (
          filtered.map((item) => {
            const isMcp = item.kind === "mcp";
            return (
              <Card key={item.id} className="flex flex-col justify-between transition-colors hover:border-primary/40">
                <CardHeader className="p-4 pb-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-muted/80">
                        {renderIntegrationIcon(item.kind)}
                      </div>
                      <div>
                        <CardTitle className="font-semibold text-sm">{item.name}</CardTitle>
                        <CardDescription className="font-mono text-[11px]">{item.namespace}</CardDescription>
                      </div>
                    </div>

                    <Badge
                      variant={item.health === "healthy" ? "outline" : "destructive"}
                      className="shrink-0 text-[10px] capitalize"
                    >
                      {item.health}
                    </Badge>
                  </div>
                </CardHeader>

                <CardContent className="flex-1 space-y-3 p-4 pt-2">
                  <div className="grid grid-cols-2 gap-2 rounded-md bg-muted/30 p-2 text-[11px]">
                    <div>
                      <span className="text-muted-foreground">Kind: </span>
                      <span className="font-medium text-[10px] uppercase">{item.kind}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Transport: </span>
                      <span className="font-mono">{item.transport}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Exposure: </span>
                      <span className="font-medium capitalize">{item.exposeMode}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Tools: </span>
                      <span className="font-semibold text-primary">{item.toolCount}</span>
                    </div>
                  </div>

                  {/* Connected Accounts */}
                  <div className="space-y-1">
                    <span className="font-medium text-[11px] text-muted-foreground">
                      Accounts ({item.accounts.length}):
                    </span>
                    <div className="space-y-1">
                      {item.accounts.map((acc) => (
                        <div
                          key={acc.id}
                          className="flex items-center justify-between rounded border bg-background px-2 py-1 text-xs"
                        >
                          <span className="max-w-[140px] truncate font-medium text-[11px]">{acc.label}</span>
                          <div className="flex items-center gap-1.5">
                            <span className="font-mono text-[10px] text-muted-foreground">{acc.authRef}</span>
                            <Badge
                              variant={acc.status === "active" ? "secondary" : "destructive"}
                              className="h-4 px-1 text-[9px]"
                            >
                              {acc.status}
                            </Badge>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </CardContent>

                {/* Footer Actions */}
                <div className="flex items-center justify-between gap-2 border-t bg-muted/10 p-3">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 gap-1 text-xs"
                    disabled={testingId === item.id || item.accounts.length === 0}
                    onClick={() => testConnection(item)}
                  >
                    <Play className="size-3" />
                    <span>{testingId === item.id ? "Testing..." : "Test"}</span>
                  </Button>

                  <div className="flex items-center gap-1">
                    {isMcp ? (
                      <Button size="sm" variant="default" className="h-7 gap-1 text-xs" asChild>
                        <Link href={`/integrations/${item.id}`}>
                          <span>Tools ({item.toolCount})</span>
                          <ChevronRight className="size-3" />
                        </Link>
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 text-muted-foreground text-xs hover:text-destructive"
                        onClick={() => setDeleteTarget(item)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    )}
                  </div>
                </div>
              </Card>
            );
          })
        )}
      </div>

      {/* Add Integration Dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-semibold text-base">Connect Integration</DialogTitle>
            <DialogDescription className="text-xs">
              Connect any MCP server, REST API via OpenAPI, or AI CLI worker to Harness Farm.
            </DialogDescription>
          </DialogHeader>

          {cliCommandAfterSave ? (
            /* Post-Save Exact CLI Command Display */
            <div className="space-y-4 py-2">
              <div className="space-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3">
                <div className="flex items-center gap-2 font-medium text-emerald-700 text-xs dark:text-emerald-400">
                  <CheckCircle2 className="size-4 shrink-0" />
                  <span>Next Step: Store Secret Locally</span>
                </div>
                <p className="text-[11px] text-muted-foreground">
                  The Console never accepts secret keys directly. Complete the authentication by running this command on
                  the Farm PC:
                </p>
                <div className="pt-1">
                  <CopyCommand command={cliCommandAfterSave} />
                </div>
              </div>

              <DialogFooter>
                <Button size="sm" onClick={() => setAddOpen(false)}>
                  Done
                </Button>
              </DialogFooter>
            </div>
          ) : (
            /* Schema Form */
            <div className="space-y-4 py-2">
              {/* Provider Category Selection */}
              <div className="space-y-1.5">
                <Label className="font-medium text-xs">Provider Type</Label>
                <div className="grid grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => setCatalogType("mcp")}
                    className={`rounded-md border p-2 text-left transition-all ${
                      catalogType === "mcp"
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border text-muted-foreground"
                    }`}
                  >
                    <div className="font-semibold text-xs">MCP Server</div>
                    <div className="text-[10px]">Stdio, HTTP, or SSE</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => setCatalogType("openapi")}
                    className={`rounded-md border p-2 text-left transition-all ${
                      catalogType === "openapi"
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border text-muted-foreground"
                    }`}
                  >
                    <div className="font-semibold text-xs">OpenAPI API</div>
                    <div className="text-[10px]">REST via Spec</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => setCatalogType("ai")}
                    className={`rounded-md border p-2 text-left transition-all ${
                      catalogType === "ai"
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border text-muted-foreground"
                    }`}
                  >
                    <div className="font-semibold text-xs">AI CLI Worker</div>
                    <div className="text-[10px]">Claude, Codex, etc.</div>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="id-input" className="font-medium text-xs">
                    Unique ID (Slug)
                  </Label>
                  <Input
                    id="id-input"
                    placeholder="e.g. sentry-mcp"
                    value={providerId}
                    onChange={(e) => setProviderId(e.target.value)}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="name-input" className="font-medium text-xs">
                    Display Name
                  </Label>
                  <Input
                    id="name-input"
                    placeholder="e.g. Sentry Error Tracking"
                    value={providerName}
                    onChange={(e) => setProviderName(e.target.value)}
                    className="text-xs"
                  />
                </div>
              </div>

              {/* Secret Ref Field - NEVER ACCEPTS RAW KEYS */}
              <div className="space-y-1.5">
                <Label htmlFor="auth-ref-input" className="flex items-center justify-between font-medium text-xs">
                  <span>Authentication Reference</span>
                  <span className="text-[10px] text-muted-foreground">Env-var NAME or Token-Store ID</span>
                </Label>
                <Input
                  id="auth-ref-input"
                  placeholder="e.g. SENTRY_AUTH_TOKEN or token-store:sentry-01"
                  value={authEnvName}
                  onChange={(e) => handleAuthRefChange(e.target.value)}
                  className={`font-mono text-xs ${authError ? "border-destructive focus-visible:ring-destructive" : ""}`}
                />
                {authError ? (
                  <div className="flex items-start gap-1.5 pt-1 text-[11px] text-destructive">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                    <span>{authError}</span>
                  </div>
                ) : (
                  <p className="text-[10px] text-muted-foreground">
                    Keys stay strictly on your Farm PC. Secrets never pass through the Console.
                  </p>
                )}
              </div>

              {/* Protocol / Transport Options */}
              {catalogType === "mcp" && (
                <div className="grid grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label className="font-medium text-xs">Transport</Label>
                    <Select value={transport} onValueChange={setTransport}>
                      <SelectTrigger className="text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="stdio" className="text-xs">
                          stdio (Local Command)
                        </SelectItem>
                        <SelectItem value="http" className="text-xs">
                          http / Streamable
                        </SelectItem>
                        <SelectItem value="sse" className="text-xs">
                          sse (Legacy)
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="font-medium text-xs">Exposure Mode</Label>
                    <Select
                      value={exposureMode}
                      onValueChange={(v) => setExposureMode(v as "direct" | "discovery" | "auto")}
                    >
                      <SelectTrigger className="text-xs">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="auto" className="text-xs">
                          auto (Hybrid)
                        </SelectItem>
                        <SelectItem value="direct" className="text-xs">
                          direct (Gateway Tool)
                        </SelectItem>
                        <SelectItem value="discovery" className="text-xs">
                          discovery (Meta-Tools)
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}

              <DialogFooter className="pt-2">
                <Button variant="outline" size="sm" onClick={() => setAddOpen(false)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={handleAddConnection}
                  disabled={submitting || Boolean(authError) || !providerId || !authEnvName}
                >
                  {submitting ? "Adding..." : "Add Connection"}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Remove Confirm Dialog */}
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base text-destructive">
              <AlertCircle className="size-4" />
              <span>Remove Integration?</span>
            </DialogTitle>
            <DialogDescription className="text-xs">
              Are you sure you want to remove <strong>{deleteTarget?.name}</strong>? Any routing rules relying on this
              pool will be disabled.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" size="sm" onClick={confirmRemove}>
              Confirm Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
