import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader, MeterBar, StatusChip } from "@/components/kit";
import { EmptyState, ErrorState } from "@/components/farm/states";
import { Button } from "@/components/ui/button";
import { attempt } from "@/lib/farm/data";
import { fmtInt, fmtPlanUsd } from "@/lib/farm/format";
import type { Connection, PoolOverviewRow } from "@/lib/farm/types";

export const metadata: Metadata = { title: "Tools & Pools" };

export default async function ToolPoolsPage() {
  const result = await attempt(async (data) => {
    const pools = await data.listPools("tool");
    const connections = await Promise.all(
      pools.map(async (pool) => (await data.listConnections(pool.provider_id, { pageSize: 50 })).items),
    );
    return { pools, connections: connections.flat() };
  });

  if (!result.ok) return <><PageHeader title="Tools" subtitle="Tool pools, accounts, limits and health." /><ErrorState message={result.message} /></>;
  const { pools, connections } = result.data;

  return (
    <div className="space-y-5">
      <PageHeader title="Tools" subtitle="Tool pools, accounts, limits and health." />
      {pools.length === 0 ? (
        <EmptyState title="No tool pools yet" description="Add an MCP server or OpenAPI provider to see its tool pool here.">
          <Button asChild><Link href="/integrations">Add your first MCP server</Link></Button>
        </EmptyState>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {pools.map((pool: PoolOverviewRow) => {
            const accounts = connections.filter((connection: Connection) => connection.providerId === pool.provider_id);
            const failed = accounts.filter((account: Connection) => account.health.lastError);
            return (
              <article key={pool.provider_id} className="space-y-4 rounded-xl border bg-card p-5">
                <div className="flex items-start justify-between gap-3">
                  <div><h2 className="font-semibold">{pool.provider_name}</h2><p className="font-mono text-xs text-muted-foreground">{pool.provider_id}</p></div>
                  <StatusChip status={pool.health === "healthy" ? "ready" : pool.health === "disabled" ? "limited" : "failed"} label={pool.health === "healthy" ? "Ready" : pool.health === "disabled" ? "Disabled" : pool.health === "degraded" ? "Degraded" : "Down"} />
                </div>
                <MeterBar value={pool.accounts_usable} max={pool.accounts_total} label="Usable accounts" showValue caption={`${fmtInt(pool.accounts_usable)} of ${fmtInt(pool.accounts_total)} accounts`} />
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div><p className="text-xs text-muted-foreground">Calls remaining</p><p className="font-semibold tabular-nums">{pool.unlimited ? "Unlimited" : fmtInt(pool.remaining_calls)}</p></div>
                  <div><p className="text-xs text-muted-foreground">Plan per month</p><p className="font-semibold tabular-nums">{fmtPlanUsd(pool.monthly_plan_usd)}</p></div>
                </div>
                <div className="space-y-2 border-t pt-3">
                  <h3 className="text-xs font-medium text-muted-foreground">Accounts ({accounts.length})</h3>
                  {accounts.map((account: Connection) => {
                    const unit = account.units.find((usage) => usage.limit !== null);
                    const isFailure = Boolean(account.health.lastError);
                    const status = account.effectiveState === "active" ? "ready" : account.effectiveState === "needs_login" ? "needs-login" : account.effectiveState === "exhausted" ? "limited" : account.effectiveState === "circuit_open" ? "failed" : "waiting-for-you";
                    return <div key={account.id} className="space-y-1.5 rounded-lg bg-muted/30 p-2.5">
                      <div className="flex items-center justify-between gap-2"><span className="truncate text-sm">{account.label}</span><StatusChip status={status} size="sm" /></div>
                      {unit ? <MeterBar value={unit.used + unit.reserved} max={unit.limit ?? 1} label={`${unit.unit} used`} showValue caption={`${fmtInt(unit.used + unit.reserved)} / ${fmtInt(unit.limit)} ${unit.unit}`} /> : <p className="text-xs text-muted-foreground">{fmtInt(account.callsToday)} calls today</p>}
                      {isFailure && <p className="text-xs text-destructive">{account.health.lastError}</p>}
                    </div>;
                  })}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
