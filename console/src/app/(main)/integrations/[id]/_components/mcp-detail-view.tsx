"use client";

import { useState } from "react";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { ArrowLeft, CheckCircle2, Code, Lock, Play, RefreshCw, Search, Terminal, Wrench } from "lucide-react";
import { SignInDrawer } from "@/app/(main)/pools/_components/sign-in-drawer";

import { CommandChip } from "@/components/farm/command-chip";
import { CopyCommand } from "@/components/farm/copy-command";
import { useCommands } from "@/components/farm/use-command";
import { MeterBar, StatusChip } from "@/components/kit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtAbsolute } from "@/lib/farm/format";
import type { McpDetail, McpToolItem } from "@/lib/farm/integrations-data";
import type { Connection } from "@/lib/farm/types";

export function McpDetailView({ initialDetail, connections }: { initialDetail: McpDetail; connections: Connection[] }) {
  const detail = initialDetail;
  const tools = initialDetail.tools;
  const [toolSearch, setToolSearch] = useState("");
  const [inspectingSchemaTool, setInspectingSchemaTool] = useState<McpToolItem | null>(null);
  const [signInAccount, setSignInAccount] = useState<string | null>(null);
  const router = useRouter();
  const { run, pending } = useCommands();
  const syncing = Boolean(pending.sync);
  const testing = Boolean(pending.test);

  const filteredTools = tools.filter((tool) => {
    if (toolSearch) {
      const q = toolSearch.toLowerCase();
      return tool.name.toLowerCase().includes(q) || tool.description.toLowerCase().includes(q);
    }
    return true;
  });

  async function handleToggleTool(tool: McpToolItem, enabled: boolean) {
    await run(
      "set_mcp_tool_access",
      {
        provider_id: detail.integration.id,
        tool: tool.name,
        enabled,
        access: enabled ? "allow" : "deny",
      },
      { key: tool.id, label: `${enabled ? "Allow" : "Deny"} ${tool.name}` },
    );
  }

  async function handleSyncTools() {
    await run("sync_mcp_tools", { provider_id: detail.integration.id }, { key: "sync", label: "Sync MCP tools" });
  }

  async function handleTest() {
    const acc = detail.integration.accounts[0];
    if (!acc) return;
    await run("test_connection", { connection_id: acc.id }, { key: "test", label: "Test MCP connection" });
  }

  return (
    <div className="space-y-6">
      {/* Back button & top bar */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" className="gap-1.5 text-muted-foreground text-xs" asChild>
          <Link href="/integrations">
            <ArrowLeft className="size-3.5" />
            <span>Back to Integrations</span>
          </Link>
        </Button>

        <div className="flex items-center gap-2">
          <CommandChip command={pending.test} />
          <CommandChip command={pending.sync} />
          <Button variant="outline" size="sm" className="h-8 gap-1.5 text-xs" onClick={handleTest} disabled={testing}>
            <Play className="size-3" />
            <span>{testing ? "Testing..." : "Test Connection"}</span>
          </Button>

          <Button size="sm" className="h-8 gap-1.5 font-medium text-xs" onClick={handleSyncTools} disabled={syncing}>
            <RefreshCw className={`size-3 ${syncing ? "animate-spin" : ""}`} />
            <span>{syncing ? "Syncing..." : "Sync Tools"}</span>
          </Button>
        </div>
      </div>

      {/* Main summary header */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card className="flex flex-col justify-between p-4">
          <span className="text-muted-foreground text-xs">Namespace</span>
          <span className="font-mono font-semibold text-sm">{detail.integration.namespace}</span>
        </Card>
        <Card className="flex flex-col justify-between p-4">
          <span className="text-muted-foreground text-xs">Transport</span>
          <span className="font-mono font-semibold text-sm uppercase">{detail.integration.transport}</span>
        </Card>
        <Card className="flex flex-col justify-between p-4">
          <span className="text-muted-foreground text-xs">Exposure Mode</span>
          <span className="font-medium text-sm capitalize">{detail.integration.exposeMode}</span>
        </Card>
        <Card className="flex flex-col justify-between p-4">
          <span className="text-muted-foreground text-xs">Last Synced</span>
          <span className="font-mono text-xs">
            {detail.integration.lastSyncAt ? fmtAbsolute(detail.integration.lastSyncAt) : "Never"}
          </span>
        </Card>
      </div>

      {/* CLI Setup Instructions (Things only PC can do) */}
      <Card className="border-primary/20 bg-primary/5">
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2">
            <Terminal className="size-4 text-primary" />
            <CardTitle className="font-semibold text-sm">Farm worker commands</CardTitle>
          </div>
          <CardDescription className="text-xs">
            Run these commands as your Farm worker user on the machine hosting this instance.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex flex-col justify-between gap-1 text-xs sm:flex-row sm:items-center">
            <span className="text-muted-foreground">Import from desktop config:</span>
            <CopyCommand command={detail.cliCommands.importCmd} />
          </div>
          <div className="flex flex-col justify-between gap-1 text-xs sm:flex-row sm:items-center">
            <span className="text-muted-foreground">Authenticate account session:</span>
            <CopyCommand command={detail.cliCommands.loginCmd} />
          </div>
          <div className="flex flex-col justify-between gap-1 text-xs sm:flex-row sm:items-center">
            <span className="text-muted-foreground">Store secret token securely:</span>
            <CopyCommand command={detail.cliCommands.secretCmd} />
          </div>
        </CardContent>
      </Card>

      {/* Tools Table with Allow/Deny toggles & Schema Inspection */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
            <div>
              <CardTitle className="font-semibold text-base">Exposed MCP Tools</CardTitle>
              <CardDescription className="text-xs">
                Per-tool allow/deny configuration. Disabling a tool prevents callers from invoking it through the
                gateway.
              </CardDescription>
            </div>
            <div className="relative w-full sm:w-64">
              <Search className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
              <Input
                placeholder="Filter tools..."
                value={toolSearch}
                onChange={(e) => setToolSearch(e.target.value)}
                className="h-8 pl-8 font-mono text-xs"
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="px-4 pt-3">
            {detail.toolsTruncated ? (
              <p role="status">Showing the first 1,000 tools. Use MCP discovery for the full catalogue.</p>
            ) : null}
            <MeterBar
              value={detail.integration.accounts.filter((account) => account.status === "active").length}
              max={detail.integration.accounts.length}
              label="Active accounts"
              showValue
              caption={`${detail.integration.accounts.filter((account) => account.status === "active").length} of ${detail.integration.accounts.length} accounts active`}
            />
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[220px]">Tool Name</TableHead>
                <TableHead>Description</TableHead>
                <TableHead className="w-[110px]">Permission</TableHead>
                <TableHead className="w-[100px] text-right">Schema</TableHead>
                <TableHead className="w-[90px] text-right">Allowed</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredTools.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-28 text-center text-muted-foreground text-xs">
                    No tools found for this server. Click &quot;Sync Tools&quot; to fetch from remote.
                  </TableCell>
                </TableRow>
              ) : (
                filteredTools.map((tool) => (
                  <TableRow key={tool.id} className={!tool.enabled ? "bg-muted/20 opacity-60" : ""}>
                    <TableCell>
                      <div className="flex items-center gap-1.5 font-mono font-semibold text-foreground text-xs">
                        <Wrench className="size-3 text-primary" />
                        <span>{tool.name}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">{tool.description}</TableCell>
                    <TableCell>
                      {tool.readOnly ? (
                        <Badge variant="outline" className="gap-1 border-emerald-500/30 text-[10px] text-emerald-600">
                          <CheckCircle2 className="size-2.5" />
                          <span>read-only</span>
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="gap-1 text-[10px]">
                          <Lock className="size-2.5 text-amber-500" />
                          <span>mutating</span>
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 gap-1 font-mono text-xs"
                        onClick={() => setInspectingSchemaTool(tool)}
                      >
                        <Code className="size-3" />
                        <span>JSON</span>
                      </Button>
                    </TableCell>
                    <TableCell className="text-right">
                      <Switch
                        checked={tool.enabled === true}
                        disabled={tool.enabled === null || Boolean(pending[tool.id])}
                        onCheckedChange={(checked) => handleToggleTool(tool, checked)}
                        aria-label={`Toggle tool ${tool.name}`}
                      />
                      {tool.enabled === null ? (
                        <span className="text-muted-foreground text-xs">Advanced policy</span>
                      ) : null}
                      <CommandChip command={pending[tool.id]} />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Connected Accounts */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="font-semibold text-base">Connected Accounts</CardTitle>
          <CardDescription className="text-xs">
            Worker accounts and authentication pools serving this integration.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Account Label</TableHead>
                <TableHead>Account ID</TableHead>
                <TableHead>Auth Ref</TableHead>
                <TableHead>Concurrency</TableHead>
                <TableHead>Last Success</TableHead>
                <TableHead>Calls / limits</TableHead>
                <TableHead className="text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.integration.accounts.map((acc) => {
                const connection = connections.find((item) => item.id === acc.id);
                const usage = connection?.units.find((unit) => unit.limit !== null);
                const failed = Boolean(connection?.health.lastError);
                const status =
                  acc.status === "active"
                    ? "ready"
                    : acc.status === "needs_login"
                      ? "needs-login"
                      : acc.status === "exhausted"
                        ? "limited"
                        : "waiting-for-you";
                return (
                  <TableRow key={acc.id}>
                    <TableCell className="font-medium text-xs">{acc.label}</TableCell>
                    <TableCell className="font-mono text-xs">{acc.id}</TableCell>
                    <TableCell className="font-mono text-muted-foreground text-xs">{acc.authRef}</TableCell>
                    <TableCell className="text-xs">{acc.concurrency} parallel</TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {acc.lastSuccessAt ? fmtAbsolute(acc.lastSuccessAt) : "Never"}
                    </TableCell>
                    <TableCell>
                      {usage ? (
                        <MeterBar
                          value={usage.used + usage.reserved}
                          max={usage.limit ?? 1}
                          label={`${usage.unit} used`}
                          showValue
                          caption={`${usage.used + usage.reserved} / ${usage.limit} ${usage.unit}`}
                        />
                      ) : connection ? (
                        <span>{connection.callsToday} calls today</span>
                      ) : (
                        "—"
                      )}
                      {failed && <p className="mt-1 text-xs text-destructive">{connection?.health.lastError}</p>}
                    </TableCell>
                    <TableCell className="text-right">
                      <StatusChip status={status} label={acc.status.replaceAll("_", " ")} size="sm" />
                      {acc.authRef.startsWith("token-store:") && (
                        <Button variant="outline" size="sm" onClick={() => setSignInAccount(acc.id)}>
                          {acc.status === "needs_login" ? "Sign in" : "Reconnect"}
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

      <SignInDrawer
        open={signInAccount !== null}
        onOpenChange={(open) => !open && setSignInAccount(null)}
        connectionId={signInAccount ?? undefined}
        providerId={detail.integration.id}
        providerName={detail.integration.name}
        title={`Connect ${detail.integration.name}`}
        mcpOAuth
        onDone={() => router.refresh()}
      />

      {/* Tool Input Schema Inspector Modal */}
      <Dialog open={inspectingSchemaTool !== null} onOpenChange={(open) => !open && setInspectingSchemaTool(null)}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 font-mono text-base">
              <Code className="size-4 text-primary" />
              <span>{inspectingSchemaTool?.name} Input Schema</span>
            </DialogTitle>
            <DialogDescription className="text-xs">{inspectingSchemaTool?.description}</DialogDescription>
          </DialogHeader>

          {inspectingSchemaTool && (
            <div className="space-y-3 pt-2">
              <div className="max-h-80 overflow-auto rounded-lg border bg-muted/60 p-3 font-mono text-xs">
                <pre>{JSON.stringify(inspectingSchemaTool.inputSchema, null, 2)}</pre>
              </div>
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span>Schema Hash: {inspectingSchemaTool.schemaHash}</span>
                <span>Synced: {fmtAbsolute(inspectingSchemaTool.syncedAt)}</span>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
