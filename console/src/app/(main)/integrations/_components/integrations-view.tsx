"use client";

import { useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCircle2,
  ChevronRight,
  Layers,
  Play,
  Plus,
  Search,
  Server,
  Trash2,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { CopyCommand } from "@/components/farm/copy-command";
import { EmptyState } from "@/components/farm/states";
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
import type { ConnectionStatus } from "@/lib/farm/types";

type AddWhat = "mcp" | "ai" | "existing" | "openapi";
type DialogStep = "what" | "form" | "result";

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
  const [dialogStep, setDialogStep] = useState<DialogStep>("what");
  const [addWhat, setAddWhat] = useState<AddWhat>("mcp");
  const [submitting, setSubmitting] = useState(false);
  const [resultCommand, setResultCommand] = useState<string | null>(null);
  const [resultStatus, setResultStatus] = useState<string>("active");

  // Form Fields - MCP
  const [mcpId, setMcpId] = useState("");
  const [mcpName, setMcpName] = useState("");
  const [mcpNamespace, setMcpNamespace] = useState("");
  const [mcpTransport, setMcpTransport] = useState<"stdio" | "http" | "sse">("stdio");
  const [mcpCommand, setMcpCommand] = useState("");
  const [mcpArgs, setMcpArgs] = useState("");
  const [mcpEnvNames, setMcpEnvNames] = useState("");
  const [mcpCwd, setMcpCwd] = useState("");
  const [mcpUrl, setMcpUrl] = useState("");
  const [mcpHeaders, setMcpHeaders] = useState("");
  const [mcpAuth, setMcpAuth] = useState<"none" | "env" | "oauth">("none");
  const [mcpExposure, setMcpExposure] = useState<"auto" | "direct" | "discovery">("auto");

  // Form Fields - AI
  const [aiCli, setAiCli] = useState<"claude" | "codex" | "gemini" | "hermes">("claude");
  const [aiAccountId, setAiAccountId] = useState("");
  const [aiLabel, setAiLabel] = useState("");
  const [aiModels, setAiModels] = useState("claude-3-7-sonnet");
  const [aiMaxParallel, setAiMaxParallel] = useState(1);

  // Form Fields - Existing Integration
  const [existingProviderId, setExistingProviderId] = useState("");
  const [existingConnId, setExistingConnId] = useState("");
  const [existingLabel, setExistingLabel] = useState("");
  const [existingAuthRef, setExistingAuthRef] = useState("");

  // Form Fields - OpenAPI
  const [openApiId, setOpenApiId] = useState("");
  const [openApiName, setOpenApiName] = useState("");
  const [openApiSpec, setOpenApiSpec] = useState("");
  const [openApiAuthEnv, setOpenApiAuthEnv] = useState("");

  // Common secret validation error
  const [secretError, setSecretError] = useState<string | null>(null);

  // Delete confirm state
  const [deleteTarget, setDeleteTarget] = useState<IntegrationSummary | null>(null);
  const [forceRemove, setForceRemove] = useState(false);
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

  function validateSecretInput(val: string) {
    if (looksLikeSecret(val)) {
      setSecretError(SECRET_REFUSAL);
      return false;
    }
    setSecretError(null);
    return true;
  }

  function resetForm() {
    setDialogStep("what");
    setSubmitting(false);
    setResultCommand(null);
    setResultStatus("active");
    setSecretError(null);

    setMcpId("");
    setMcpName("");
    setMcpNamespace("");
    setMcpTransport("stdio");
    setMcpCommand("");
    setMcpArgs("");
    setMcpEnvNames("");
    setMcpCwd("");
    setMcpUrl("");
    setMcpHeaders("");
    setMcpAuth("none");
    setMcpExposure("auto");

    setAiCli("claude");
    setAiAccountId("");
    setAiLabel("");
    setAiModels("claude-3-7-sonnet");
    setAiMaxParallel(1);

    setExistingProviderId(integrations[0]?.id ?? "");
    setExistingConnId("");
    setExistingLabel("");
    setExistingAuthRef("");

    setOpenApiId("");
    setOpenApiName("");
    setOpenApiSpec("");
    setOpenApiAuthEnv("");
  }

  // Handle Form Submission per "what" type
  async function handleSubmit() {
    setSubmitting(true);
    setSecretError(null);

    try {
      if (addWhat === "mcp") {
        const cleanId = mcpId.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-");
        if (!cleanId) {
          toast.error("Please specify a provider ID");
          return;
        }
        if (mcpTransport === "stdio" && !mcpCommand.trim()) {
          toast.error("Command is required for stdio transport");
          return;
        }
        if (mcpTransport !== "stdio" && !mcpUrl.trim()) {
          toast.error("URL is required for HTTP/SSE transport");
          return;
        }
        if (mcpEnvNames && !validateSecretInput(mcpEnvNames)) return;
        if (mcpHeaders && !validateSecretInput(mcpHeaders)) return;

        const argsList = mcpArgs.trim() ? mcpArgs.trim().split(/\s+/) : [];
        const envList = mcpEnvNames.trim() ? mcpEnvNames.trim().split(/[,\s]+/).filter(Boolean) : [];
        const headersDict: Record<string, string> = {};
        if (mcpHeaders.trim()) {
          for (const line of mcpHeaders.split(/[\n,]+/)) {
            const [k, v] = line.split("=").map((s) => s.trim());
            if (k && v) headersDict[k] = v;
          }
        }

        const payload: Record<string, unknown> = {
          provider_id: cleanId,
          name: mcpName.trim() || cleanId,
          kind: "tool",
          executor: "mcp",
          command: mcpTransport === "stdio" ? mcpCommand.trim() : undefined,
          args: mcpTransport === "stdio" ? argsList : [],
          cwd: mcpTransport === "stdio" && mcpCwd.trim() ? mcpCwd.trim() : undefined,
          env: mcpTransport === "stdio" ? envList : undefined,
          url: mcpTransport !== "stdio" ? mcpUrl.trim() : undefined,
          headers: mcpTransport !== "stdio" ? headersDict : undefined,
          auth: mcpAuth,
          namespace: mcpNamespace.trim() || cleanId,
          exposure: mcpExposure,
          status: mcpAuth === "oauth" ? "needs_login" : "active",
        };

        const res = await submitCommand("add_provider", payload);
        if (!res.ok) throw new Error(res.error || "Failed to add MCP server");

        const cmdResult = res.data.result as Record<string, unknown> | undefined;
        const nextStep = typeof cmdResult?.next_step === "string" ? cmdResult.next_step : "farm mcp sync";
        let status = mcpAuth === "oauth" ? "needs_login" : "active";
        if (typeof cmdResult?.status === "string") {
          status = cmdResult.status;
        }
        const toolsCount = typeof cmdResult?.tools_count === "number" ? cmdResult.tools_count : 1;

        setResultCommand(nextStep);
        setResultStatus(status);
        setDialogStep("result");

        // Optimistically add to list
        const newItem: IntegrationSummary = {
          id: cleanId,
          name: mcpName.trim() || cleanId,
          kind: "mcp",
          executor: "mcp",
          transport: mcpTransport,
          namespace: mcpNamespace.trim() || cleanId,
          exposeMode: mcpExposure,
          health: "healthy",
          toolCount: toolsCount,
          accountsCount: 1,
          accounts: [
            {
              id: `${cleanId}-01`,
              label: `${mcpName.trim() || cleanId} Primary`,
              authRef: mcpAuth === "oauth" ? `token-store:${cleanId}-01` : "cli:none",
              status: status as ConnectionStatus,
              priority: 100,
              concurrency: 1,
              lastSuccessAt: null,
            },
          ],
          lastSyncAt: new Date().toISOString(),
          config: {},
        };
        setIntegrations((prev) => [newItem, ...prev.filter((i) => i.id !== cleanId)]);
        toast.success(`MCP server "${cleanId}" registered.`);
      } else if (addWhat === "ai") {
        const cleanAccId = aiAccountId.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-");
        if (!cleanAccId) {
          toast.error("Please enter an account ID (e.g. claude-02)");
          return;
        }

        const modelsList = aiModels.split(",").map((s) => s.trim()).filter(Boolean);
        const providerId = aiCli;

        const payload: Record<string, unknown> = {
          provider_id: providerId,
          name: `${aiCli.toUpperCase()} AI Pool`,
          kind: "ai",
          executor: "cli_agent",
          cli: aiCli,
          account_id: cleanAccId,
          label: aiLabel.trim() || cleanAccId,
          models: modelsList,
          max_parallel: aiMaxParallel,
          status: "needs_login",
        };

        const res = await submitCommand("add_provider", payload);
        if (!res.ok) throw new Error(res.error || "Failed to add AI account");

        const cmdResult = res.data.result as Record<string, unknown> | undefined;
        const nextStep =
          typeof cmdResult?.next_step === "string" ? cmdResult.next_step : `farm ai login ${cleanAccId}`;

        setResultCommand(nextStep);
        setResultStatus("needs_login");
        setDialogStep("result");

        // Optimistically add/update in list
        const newAcc = {
          id: cleanAccId,
          label: aiLabel.trim() || cleanAccId,
          authRef: `cli:${cleanAccId}`,
          status: "needs_login" as const,
          priority: 100,
          concurrency: aiMaxParallel,
          lastSuccessAt: null,
        };

        setIntegrations((prev) => {
          const existing = prev.find((i) => i.id === providerId);
          if (existing) {
            return prev.map((i) =>
              i.id === providerId
                ? {
                    ...i,
                    accountsCount: i.accounts.length + 1,
                    accounts: [...i.accounts.filter((a) => a.id !== cleanAccId), newAcc],
                  }
                : i,
            );
          }
          const newItem: IntegrationSummary = {
            id: providerId,
            name: `${aiCli.toUpperCase()} AI Pool`,
            kind: "ai",
            executor: "cli_agent",
            transport: "cli",
            namespace: providerId,
            exposeMode: "direct",
            health: "healthy",
            toolCount: 0,
            accountsCount: 1,
            accounts: [newAcc],
            lastSyncAt: new Date().toISOString(),
            config: {},
          };
          return [newItem, ...prev];
        });
        toast.success(`AI account "${cleanAccId}" registered.`);
      } else if (addWhat === "existing") {
        if (!existingProviderId) {
          toast.error("Please select a provider");
          return;
        }
        const cleanConnId = existingConnId.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-");
        if (!cleanConnId) {
          toast.error("Please enter a connection ID");
          return;
        }
        if (!existingAuthRef.trim()) {
          toast.error("Please enter an authentication reference (e.g. env:KEY or token-store:id)");
          return;
        }
        if (!validateSecretInput(existingAuthRef)) return;

        const authRef =
          existingAuthRef.startsWith("env:") ||
          existingAuthRef.startsWith("token-store:") ||
          existingAuthRef.startsWith("cli:")
            ? existingAuthRef
            : `env:${existingAuthRef.trim()}`;

        const isTokenStore = authRef.startsWith("token-store:");
        const res = await submitCommand("add_connection", {
          provider_id: existingProviderId,
          id: cleanConnId,
          label: existingLabel.trim() || cleanConnId,
          auth_ref: authRef,
          scope: ["internal"],
          priority: 100,
          concurrency: 1,
          status: isTokenStore ? "needs_login" : "active",
          plan: { name: "Default", price_usd: 0 },
          meta: {},
          units: {},
        });
        if (!res.ok) throw new Error(res.error || "Failed to add connection");

        const postCmd = isTokenStore
          ? `farm mcp login ${cleanConnId}`
          : `farm set-secret ${authRef.replace(/^env:/, "").toUpperCase()}`;

        setResultCommand(postCmd);
        setResultStatus(isTokenStore ? "needs_login" : "active");
        setDialogStep("result");

        // Optimistically update
        setIntegrations((prev) =>
          prev.map((i) =>
            i.id === existingProviderId
              ? {
                  ...i,
                  accountsCount: i.accounts.length + 1,
                  accounts: [
                    ...i.accounts.filter((a) => a.id !== cleanConnId),
                    {
                      id: cleanConnId,
                      label: existingLabel.trim() || cleanConnId,
                      authRef,
                      status: isTokenStore ? "needs_login" : "active",
                      priority: 100,
                      concurrency: 1,
                      lastSuccessAt: null,
                    },
                  ],
                }
              : i,
          ),
        );
        toast.success(`Account "${cleanConnId}" added.`);
      } else if (addWhat === "openapi") {
        const cleanId = openApiId.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-");
        if (!cleanId) {
          toast.error("Please enter a provider ID");
          return;
        }
        if (!openApiSpec.trim()) {
          toast.error("Please enter a spec URL or path");
          return;
        }
        if (openApiAuthEnv && !validateSecretInput(openApiAuthEnv)) return;

        const payload: Record<string, unknown> = {
          provider_id: cleanId,
          name: openApiName.trim() || cleanId,
          kind: "tool",
          executor: "api",
          spec: openApiSpec.trim(),
          auth_env: openApiAuthEnv.trim() || undefined,
        };

        const res = await submitCommand("add_provider", payload);
        if (!res.ok) throw new Error(res.error || "Failed to add OpenAPI provider");

        const cmdResult = res.data.result as Record<string, unknown> | undefined;
        let nextStep = "Connection ready";
        if (typeof cmdResult?.next_step === "string") {
          nextStep = cmdResult.next_step;
        } else if (openApiAuthEnv) {
          nextStep = `farm set-secret ${openApiAuthEnv}`;
        }

        setResultCommand(nextStep);
        setResultStatus("active");
        setDialogStep("result");

        const newItem: IntegrationSummary = {
          id: cleanId,
          name: openApiName.trim() || cleanId,
          kind: "openapi",
          executor: "api",
          transport: "http",
          namespace: cleanId,
          exposeMode: "direct",
          health: "healthy",
          toolCount: 0,
          accountsCount: 1,
          accounts: [
            {
              id: `${cleanId}-01`,
              label: `${openApiName.trim() || cleanId} Primary`,
              authRef: openApiAuthEnv ? `env:${openApiAuthEnv}` : "cli:none",
              status: "active",
              priority: 100,
              concurrency: 1,
              lastSuccessAt: null,
            },
          ],
          lastSyncAt: new Date().toISOString(),
          config: {},
        };
        setIntegrations((prev) => [newItem, ...prev.filter((i) => i.id !== cleanId)]);
        toast.success(`OpenAPI provider "${cleanId}" registered.`);
      }
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
      const res = await submitCommand("test_connection", {
        connection_id: acc.id,
      });
      if (!res.ok) {
        throw new Error(res.error || "Connection test failed.");
      }
      toast.success(`Connection test succeeded for ${acc.label}.`);
      setIntegrations((prev) =>
        prev.map((i) =>
          i.id === item.id
            ? {
                ...i,
                health: "healthy",
                accounts: i.accounts.map((a) => (a.id === acc.id ? { ...a, status: "active" } : a)),
              }
            : i,
        ),
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connection test failed.");
    } finally {
      setTestingId(null);
    }
  }

  // Remove Provider
  async function confirmRemove() {
    if (!deleteTarget) return;
    try {
      const res = await submitCommand("remove_provider", {
        provider_id: deleteTarget.id,
        force: forceRemove,
      });
      if (!res.ok) {
        throw new Error(res.error || "Could not remove provider.");
      }
      setIntegrations((prev) => prev.filter((i) => i.id !== deleteTarget.id));
      toast.success(`Integration "${deleteTarget.name}" removed.`);
      setDeleteTarget(null);
      setForceRemove(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not remove provider.");
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
              resetForm();
              setAddOpen(true);
            }}
          >
            <Plus className="size-3.5" />
            <span>Add Integration</span>
          </Button>
        </div>
      </div>

      {/* Empty State when no integrations registered */}
      {integrations.length === 0 && (
        <EmptyState
          icon={Server}
          title="No integrations registered yet"
          description="Add your first MCP server, AI CLI account, or OpenAPI provider to start routing calls through the Farm."
        >
          <Button
            onClick={() => {
              resetForm();
              setAddWhat("mcp");
              setDialogStep("form");
              setAddOpen(true);
            }}
          >
            Add your first MCP server
          </Button>
        </EmptyState>
      )}

      {integrations.length > 0 && filtered.length === 0 && (
        <div className="flex h-36 flex-col items-center justify-center space-y-2 rounded-lg border p-6 text-muted-foreground text-xs">
          <Server className="size-6" />
          <span>No integrations match your search and filter criteria.</span>
        </div>
      )}

      {integrations.length > 0 && filtered.length > 0 && (
        /* Integrations Grid */
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((item) => {
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
                    {isMcp && (
                      <Button size="sm" variant="default" className="h-7 gap-1 text-xs" asChild>
                        <Link href={`/integrations/${item.id}`}>
                          <span>Tools ({item.toolCount})</span>
                          <ChevronRight className="size-3" />
                        </Link>
                      </Button>
                    )}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 text-muted-foreground text-xs hover:text-destructive"
                      aria-label={`Remove ${item.name}`}
                      onClick={() => setDeleteTarget(item)}
                    >
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {/* Add Integration Dialog */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-lg">
          {/* STEP 1: WHAT */}
          {dialogStep === "what" && (
            <>
              <DialogHeader>
                <DialogTitle className="font-semibold text-base">Add Integration</DialogTitle>
                <DialogDescription className="text-xs">
                  What would you like to connect to Harness Farm?
                </DialogDescription>
              </DialogHeader>

              <div className="grid gap-2.5 py-3">
                <button
                  type="button"
                  onClick={() => {
                    setAddWhat("mcp");
                    setDialogStep("form");
                  }}
                  className="flex items-start gap-3 rounded-lg border p-3 text-left transition-all hover:border-primary hover:bg-muted/40"
                >
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Server className="size-5" />
                  </div>
                  <div>
                    <div className="font-semibold text-xs">New MCP server</div>
                    <div className="text-[11px] text-muted-foreground">
                      Run a local stdio command (e.g. npx, uvx) or connect to an HTTP/SSE endpoint.
                    </div>
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setAddWhat("ai");
                    setDialogStep("form");
                  }}
                  className="flex items-start gap-3 rounded-lg border p-3 text-left transition-all hover:border-primary hover:bg-muted/40"
                >
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-purple-500/10 text-purple-600">
                    <Bot className="size-5" />
                  </div>
                  <div>
                    <div className="font-semibold text-xs">New AI account</div>
                    <div className="text-[11px] text-muted-foreground">
                      Connect a Claude, Codex, Gemini/agy, or Hermes CLI profile.
                    </div>
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setAddWhat("existing");
                    setDialogStep("form");
                  }}
                  className="flex items-start gap-3 rounded-lg border p-3 text-left transition-all hover:border-primary hover:bg-muted/40"
                  disabled={integrations.length === 0}
                >
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-blue-500/10 text-blue-600">
                    <Layers className="size-5" />
                  </div>
                  <div>
                    <div className="font-semibold text-xs">Account for an existing integration</div>
                    <div className="text-[11px] text-muted-foreground">
                      {integrations.length === 0
                        ? "Register a provider first before adding additional accounts."
                        : "Add an additional account/credential to an already registered provider."}
                    </div>
                  </div>
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setAddWhat("openapi");
                    setDialogStep("form");
                  }}
                  className="flex items-start gap-3 rounded-lg border p-3 text-left transition-all hover:border-primary hover:bg-muted/40"
                >
                  <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-amber-500/10 text-amber-600">
                    <Zap className="size-5" />
                  </div>
                  <div>
                    <div className="font-semibold text-xs">OpenAPI provider</div>
                    <div className="text-[11px] text-muted-foreground">
                      Register a REST API from an OpenAPI v3 spec URL or file.
                    </div>
                  </div>
                </button>
              </div>

              <DialogFooter>
                <Button variant="outline" size="sm" onClick={() => setAddOpen(false)}>
                  Cancel
                </Button>
              </DialogFooter>
            </>
          )}

          {/* STEP 2: FORM */}
          {dialogStep === "form" && (
            <>
              <DialogHeader>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0"
                    onClick={() => setDialogStep("what")}
                  >
                    <ArrowLeft className="size-3.5" />
                  </Button>
                  <DialogTitle className="font-semibold text-base">
                    {addWhat === "mcp" && "Add MCP Server"}
                    {addWhat === "ai" && "Add AI CLI Account"}
                    {addWhat === "existing" && "Add Account to Existing Integration"}
                    {addWhat === "openapi" && "Add OpenAPI Provider"}
                  </DialogTitle>
                </div>
                <DialogDescription className="text-xs">
                  {addWhat === "mcp" && "Configure stdio command or HTTP/SSE parameters. Secrets stay on your Farm PC."}
                  {addWhat === "ai" && "Configure CLI worker profile and model limits. Multi-account delegation."}
                  {addWhat === "existing" && "Connect a new credential or profile to an existing provider."}
                  {addWhat === "openapi" && "Provide OpenAPI spec and authentication environment variable."}
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-3.5 py-2">
                {/* Secret Ref Error Alert */}
                {secretError && (
                  <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2.5 text-destructive text-xs">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                    <span>{secretError}</span>
                  </div>
                )}

                {/* FORM: MCP */}
                {addWhat === "mcp" && (
                  <>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label htmlFor="mcp-id" className="text-xs">Provider Slug</Label>
                        <Input
                          id="mcp-id"
                          placeholder="e.g. sentry"
                          value={mcpId}
                          onChange={(e) => setMcpId(e.target.value)}
                          className="font-mono text-xs"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="mcp-name" className="text-xs">Display Name</Label>
                        <Input
                          id="mcp-name"
                          placeholder="e.g. Sentry Error Tracking"
                          value={mcpName}
                          onChange={(e) => setMcpName(e.target.value)}
                          className="text-xs"
                        />
                      </div>
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      <div className="space-y-1">
                        <Label className="text-xs">Transport</Label>
                        <Select
                          value={mcpTransport}
                          onValueChange={(v) => {
                            if (v === "stdio" || v === "http" || v === "sse") setMcpTransport(v);
                          }}
                        >
                          <SelectTrigger className="text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="stdio" className="text-xs">stdio (CLI)</SelectItem>
                            <SelectItem value="http" className="text-xs">http</SelectItem>
                            <SelectItem value="sse" className="text-xs">sse</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-1">
                        <Label className="text-xs">Auth</Label>
                        <Select
                          value={mcpAuth}
                          onValueChange={(v) => {
                            if (v === "none" || v === "env" || v === "oauth") setMcpAuth(v);
                          }}
                        >
                          <SelectTrigger className="text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none" className="text-xs">None</SelectItem>
                            <SelectItem value="env" className="text-xs">Env Var</SelectItem>
                            <SelectItem value="oauth" className="text-xs">OAuth</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-1">
                        <Label className="text-xs">Exposure</Label>
                        <Select
                          value={mcpExposure}
                          onValueChange={(v) => {
                            if (v === "auto" || v === "direct" || v === "discovery") setMcpExposure(v);
                          }}
                        >
                          <SelectTrigger className="text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="auto" className="text-xs">Auto</SelectItem>
                            <SelectItem value="direct" className="text-xs">Direct</SelectItem>
                            <SelectItem value="discovery" className="text-xs">Discovery</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    </div>

                    {mcpTransport === "stdio" ? (
                      <>
                        <div className="space-y-1">
                          <Label htmlFor="mcp-command" className="text-xs">Command</Label>
                          <Input
                            id="mcp-command"
                            placeholder="e.g. npx, python, uvx"
                            value={mcpCommand}
                            onChange={(e) => setMcpCommand(e.target.value)}
                            className="font-mono text-xs"
                          />
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor="mcp-args" className="text-xs">Arguments</Label>
                          <Input
                            id="mcp-args"
                            placeholder="e.g. -y @modelcontextprotocol/server-filesystem"
                            value={mcpArgs}
                            onChange={(e) => setMcpArgs(e.target.value)}
                            className="font-mono text-xs"
                          />
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor="mcp-env" className="flex items-center justify-between text-xs">
                            <span>Environment Variable Names</span>
                            <span className="text-[10px] text-muted-foreground">Comma-separated names only</span>
                          </Label>
                          <Input
                            id="mcp-env"
                            placeholder="e.g. GITHUB_TOKEN, SENTRY_AUTH_TOKEN"
                            value={mcpEnvNames}
                            onChange={(e) => {
                              setMcpEnvNames(e.target.value);
                              validateSecretInput(e.target.value);
                            }}
                            className="font-mono text-xs"
                          />
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="space-y-1">
                          <Label htmlFor="mcp-url" className="text-xs">Server URL</Label>
                          <Input
                            id="mcp-url"
                            placeholder="e.g. https://mcp.example.com/sse"
                            value={mcpUrl}
                            onChange={(e) => setMcpUrl(e.target.value)}
                            className="font-mono text-xs"
                          />
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor="mcp-headers" className="flex items-center justify-between text-xs">
                            <span>Header Mappings</span>
                            <span className="text-[10px] text-muted-foreground">Header=ENV_VAR</span>
                          </Label>
                          <Input
                            id="mcp-headers"
                            placeholder="e.g. Authorization=API_KEY"
                            value={mcpHeaders}
                            onChange={(e) => {
                              setMcpHeaders(e.target.value);
                              validateSecretInput(e.target.value);
                            }}
                            className="font-mono text-xs"
                          />
                        </div>
                      </>
                    )}
                  </>
                )}

                {/* FORM: AI */}
                {addWhat === "ai" && (
                  <>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">CLI Driver</Label>
                        <Select
                          value={aiCli}
                          onValueChange={(v) => {
                            if (v === "claude" || v === "codex" || v === "gemini" || v === "hermes") {
                              setAiCli(v);
                              if (v === "claude") setAiModels("claude-3-7-sonnet");
                              if (v === "codex") setAiModels("o3-mini");
                              if (v === "gemini") setAiModels("gemini-2.5-pro");
                              if (v === "hermes") setAiModels("hermes-3");
                            }
                          }}
                        >
                          <SelectTrigger className="text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="claude" className="text-xs">Claude (Anthropic)</SelectItem>
                            <SelectItem value="codex" className="text-xs">Codex (OpenAI)</SelectItem>
                            <SelectItem value="gemini" className="text-xs">Gemini / AGY (Google)</SelectItem>
                            <SelectItem value="hermes" className="text-xs">Hermes (Nous)</SelectItem>
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-1">
                        <Label htmlFor="ai-acc-id" className="text-xs">Account ID</Label>
                        <Input
                          id="ai-acc-id"
                          placeholder="e.g. claude-02"
                          value={aiAccountId}
                          onChange={(e) => setAiAccountId(e.target.value)}
                          className="font-mono text-xs"
                        />
                      </div>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="ai-label" className="text-xs">Account Label</Label>
                      <Input
                        id="ai-label"
                        placeholder="e.g. Claude Work Account"
                        value={aiLabel}
                        onChange={(e) => setAiLabel(e.target.value)}
                        className="text-xs"
                      />
                    </div>

                    <div className="grid grid-cols-3 gap-2">
                      <div className="col-span-2 space-y-1">
                        <Label htmlFor="ai-models" className="text-xs">Allowed Models</Label>
                        <Input
                          id="ai-models"
                          value={aiModels}
                          onChange={(e) => setAiModels(e.target.value)}
                          className="font-mono text-xs"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="ai-concurrency" className="text-xs">Max Parallel</Label>
                        <Input
                          id="ai-concurrency"
                          type="number"
                          min={1}
                          max={50}
                          value={aiMaxParallel}
                          onChange={(e) => setAiMaxParallel(Number.parseInt(e.target.value, 10) || 1)}
                          className="font-mono text-xs"
                        />
                      </div>
                    </div>
                  </>
                )}

                {/* FORM: EXISTING */}
                {addWhat === "existing" && (
                  <>
                    <div className="space-y-1">
                      <Label className="text-xs">Target Provider</Label>
                      <Select
                        value={existingProviderId}
                        onValueChange={setExistingProviderId}
                      >
                        <SelectTrigger className="text-xs">
                          <SelectValue placeholder="Select provider" />
                        </SelectTrigger>
                        <SelectContent>
                          {integrations.map((i) => (
                            <SelectItem key={i.id} value={i.id} className="text-xs">
                              {i.name} ({i.id})
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label htmlFor="existing-conn-id" className="text-xs">Connection ID</Label>
                        <Input
                          id="existing-conn-id"
                          placeholder="e.g. sentry-02"
                          value={existingConnId}
                          onChange={(e) => setExistingConnId(e.target.value)}
                          className="font-mono text-xs"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="existing-label" className="text-xs">Label</Label>
                        <Input
                          id="existing-label"
                          placeholder="e.g. Sentry Backup"
                          value={existingLabel}
                          onChange={(e) => setExistingLabel(e.target.value)}
                          className="text-xs"
                        />
                      </div>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="existing-auth" className="flex items-center justify-between text-xs">
                        <span>Authentication Reference</span>
                        <span className="text-[10px] text-muted-foreground">env:NAME or token-store:id</span>
                      </Label>
                      <Input
                        id="existing-auth"
                        placeholder="e.g. env:SENTRY_AUTH_TOKEN_2"
                        value={existingAuthRef}
                        onChange={(e) => {
                          setExistingAuthRef(e.target.value);
                          validateSecretInput(e.target.value);
                        }}
                        className="font-mono text-xs"
                      />
                    </div>
                  </>
                )}

                {/* FORM: OPENAPI */}
                {addWhat === "openapi" && (
                  <>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label htmlFor="openapi-id" className="text-xs">Provider Slug</Label>
                        <Input
                          id="openapi-id"
                          placeholder="e.g. petstore"
                          value={openApiId}
                          onChange={(e) => setOpenApiId(e.target.value)}
                          className="font-mono text-xs"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="openapi-name" className="text-xs">Display Name</Label>
                        <Input
                          id="openapi-name"
                          placeholder="e.g. Swagger Petstore"
                          value={openApiName}
                          onChange={(e) => setOpenApiName(e.target.value)}
                          className="text-xs"
                        />
                      </div>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="openapi-spec" className="text-xs">Spec URL or Path</Label>
                      <Input
                        id="openapi-spec"
                        placeholder="e.g. https://petstore.swagger.io/v2/swagger.json"
                        value={openApiSpec}
                        onChange={(e) => setOpenApiSpec(e.target.value)}
                        className="font-mono text-xs"
                      />
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="openapi-auth" className="flex items-center justify-between text-xs">
                        <span>Auth Env Variable Name</span>
                        <span className="text-[10px] text-muted-foreground">Name only, no values</span>
                      </Label>
                      <Input
                        id="openapi-auth"
                        placeholder="e.g. PETSTORE_API_KEY"
                        value={openApiAuthEnv}
                        onChange={(e) => {
                          setOpenApiAuthEnv(e.target.value);
                          validateSecretInput(e.target.value);
                        }}
                        className="font-mono text-xs"
                      />
                    </div>
                  </>
                )}
              </div>

              <DialogFooter className="pt-2">
                <Button variant="outline" size="sm" onClick={() => setDialogStep("what")}>
                  Back
                </Button>
                <Button
                  size="sm"
                  onClick={handleSubmit}
                  disabled={submitting || Boolean(secretError)}
                >
                  {submitting ? "Adding..." : "Add Integration"}
                </Button>
              </DialogFooter>
            </>
          )}

          {/* STEP 3: RESULT & NEXT STEP */}
          {dialogStep === "result" && (
            <>
              <DialogHeader>
                <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400">
                  <CheckCircle2 className="size-5 shrink-0" />
                  <DialogTitle className="font-semibold text-base">Integration Added</DialogTitle>
                </div>
                <DialogDescription className="text-xs">
                  The integration has been registered with status:{" "}
                  <Badge variant={resultStatus === "active" ? "secondary" : "destructive"} className="text-[10px]">
                    {resultStatus}
                  </Badge>
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-3 py-3">
                <div className="space-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3">
                  <div className="font-medium text-emerald-700 text-xs dark:text-emerald-400">
                    Next Step on Farm PC:
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    Complete authentication by running the following command in your terminal:
                  </p>
                  {resultCommand && (
                    <div className="pt-1">
                      <CopyCommand command={resultCommand} />
                    </div>
                  )}
                </div>
              </div>

              <DialogFooter>
                <Button size="sm" onClick={() => setAddOpen(false)}>
                  Done
                </Button>
              </DialogFooter>
            </>
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
              Are you sure you want to remove <strong>{deleteTarget?.name}</strong> ({deleteTarget?.id})?
              All routes, capabilities, and accounts for this provider will be deleted.
            </DialogDescription>
          </DialogHeader>

          <div className="flex items-center space-x-2 py-2">
            <input
              type="checkbox"
              id="force-remove"
              checked={forceRemove}
              onChange={(e) => setForceRemove(e.target.checked)}
              className="rounded border"
            />
            <Label htmlFor="force-remove" className="text-muted-foreground text-xs">
              Force remove (cancel active jobs and quota reservations)
            </Label>
          </div>

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
