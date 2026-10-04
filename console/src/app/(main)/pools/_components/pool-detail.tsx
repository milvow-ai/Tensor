import { notFound, redirect } from "next/navigation";

import { cn } from "cn";
import { Activity, DollarSign, Server, Zap } from "lucide-react";

import { CommandsFeed } from "@/components/farm/commands-feed";
import { CommandsProvider } from "@/components/farm/commands-provider";
import { KpiCard } from "@/components/farm/kpi-card";
import { PageHeader } from "@/components/farm/page-header";
import { ErrorState } from "@/components/farm/states";
import { ToneBadge } from "@/components/farm/status";
import { AI_CLI_INFO, AI_CLIS, type AiCli, commandConcernsPool } from "@/lib/farm/command-meta";
import { attempt } from "@/lib/farm/data";
import { fmtCompact, fmtPct, fmtPlanUsd, fmtUsd } from "@/lib/farm/format";
import { HEALTH_META, poolHref, poolsIndexHref, TONE } from "@/lib/farm/state";
import type { ProviderKind } from "@/lib/farm/types";

import { type ListParams, toSearch } from "../_lib/list-params";
import { AccountsTable } from "./accounts-table";
import { AddAccountDialog } from "./add-account-dialog";
import { AddAiAccountDialog } from "./add-ai-account-dialog";
import { PoolLive } from "./pool-live";
import { StrategySelectLazy } from "./strategy-select-lazy";

function cliFor(providerId: string): AiCli | undefined {
  return AI_CLIS.find((cli) => AI_CLI_INFO[cli].providerId === providerId);
}

export async function PoolDetailView({ id, kind, params }: { id: string; kind: ProviderKind; params: ListParams }) {
  const [poolResult, listResult, tailResult, commandsResult] = await Promise.all([
    attempt((data) => data.getPool(id)),
    attempt((data) => data.listConnections(id, params)),
    // The highest priority in the pool, so a new account defaults to "last in line".
    attempt((data) => data.listConnections(id, { page: 1, pageSize: 1, sort: "priority", dir: "desc" })),
    attempt((data) => data.listCommands(60)),
  ]);

  const crumbs = [{ label: kind === "ai" ? "AI Pools" : "Tools & Pools", href: poolsIndexHref(kind) }, { label: id }];

  if (!poolResult.ok || !listResult.ok) {
    const failed = poolResult.ok ? listResult : poolResult;
    const message = failed.ok ? "" : failed.message;
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title={id} crumbs={crumbs} />
        <ErrorState message={message} />
      </div>
    );
  }

  const detail = poolResult.data;
  if (!detail) notFound();
  const { pool, spend, routes } = detail;
  // A tool pool opened through the AI URL (or the other way round) goes to its own page.
  if (pool.kind !== kind) redirect(poolHref(pool));

  const crumbsWithName = [crumbs[0] as (typeof crumbs)[number], { label: pool.provider_name }];
  const list = listResult.data;
  const pageCount = Math.max(Math.ceil(list.total / list.pageSize), 1);
  if (list.items.length === 0 && list.total > 0)
    redirect(`${poolHref(pool)}${toSearch({ ...params, page: pageCount })}`);

  const maxPriority = tailResult.ok ? (tailResult.data.items[0]?.priority ?? 0) : 0;
  const nextPriority = maxPriority + 1;
  const suggestedId = `${pool.provider_id}-${String(list.total + 1).padStart(2, "0")}`;
  const health = HEALTH_META[pool.health];
  const cli = cliFor(pool.provider_id);
  const budget = spend?.budget_usd ?? null;
  const overBudget = budget !== null && budget > 0 && (spend?.forecast_usd ?? 0) > budget;

  return (
    <CommandsProvider>
      <PoolLive />
      <div className="flex flex-col gap-4 md:gap-5">
        <PageHeader
          title={pool.provider_name}
          crumbs={crumbsWithName}
          description={
            <>
              {kind === "ai" ? "AI pool" : "Tool pool"} using the <span className="font-mono">{pool.executor}</span>{" "}
              executor. {pool.accounts_total} {pool.accounts_total === 1 ? "account" : "accounts"}.
            </>
          }
          badge={<ToneBadge tone={health.tone}>{health.label}</ToneBadge>}
          actions={
            kind === "ai" ? (
              <AddAiAccountDialog defaultCli={cli} />
            ) : (
              <AddAccountDialog
                providerId={pool.provider_id}
                providerName={pool.provider_name}
                suggestedId={suggestedId}
                nextPriority={nextPriority}
              />
            )
          }
        />

        <section aria-label="Pool totals" className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          <KpiCard
            label="Accounts usable"
            icon={Server}
            value={pool.accounts_usable}
            suffix={`/ ${pool.accounts_total}`}
          >
            <p className="text-muted-foreground">
              {pool.accounts_paused} paused, {pool.accounts_exhausted} exhausted, {pool.accounts_needs_login} need login
            </p>
          </KpiCard>
          <KpiCard
            label="Calls left"
            icon={Zap}
            value={pool.unlimited ? "Unlimited" : fmtCompact(pool.remaining_calls)}
          >
            <p className="text-muted-foreground">Across usable accounts, from their tightest limit.</p>
          </KpiCard>
          <KpiCard
            label="Plans"
            icon={Activity}
            value={pool.monthly_plan_usd > 0 ? fmtPlanUsd(pool.monthly_plan_usd) : "Free"}
            suffix={pool.monthly_plan_usd > 0 ? "/ month" : undefined}
          >
            <p className="text-muted-foreground">Sum of the accounts' plan prices.</p>
          </KpiCard>
          <KpiCard
            label="Spend this month"
            icon={DollarSign}
            value={fmtUsd(spend?.spend_usd ?? 0, { whole: true })}
            suffix={budget ? `of ${fmtUsd(budget, { whole: true })}` : "no budget set"}
          >
            <p className={cn(overBudget ? TONE.warn.text : "text-muted-foreground")}>
              Forecast {fmtUsd(spend?.forecast_usd ?? 0, { whole: true })}
              {budget ? ` (${fmtPct((spend?.forecast_usd ?? 0) / budget)} of budget)` : ""}
            </p>
          </KpiCard>
        </section>

        <div className="flex flex-col gap-4 rounded-xl border bg-card p-4 sm:flex-row sm:items-start sm:justify-between">
          <StrategySelectLazy providerId={pool.provider_id} current={pool.default_strategy} />
          <div className="min-w-0 text-xs sm:max-w-md sm:text-right">
            <div className="text-muted-foreground">Used by</div>
            {routes.length === 0 ? (
              <p className="mt-1 text-muted-foreground">No capability routes to this pool yet.</p>
            ) : (
              <ul className="mt-1 flex flex-wrap gap-1.5 sm:justify-end">
                {routes.map((route) => (
                  <li
                    key={route.capability}
                    className="rounded-md border bg-background px-1.5 py-0.5 font-mono text-[11px]"
                  >
                    {route.capability} <span className="text-muted-foreground">#{route.route_position}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <AccountsTable items={list.items} total={list.total} params={params} variant={kind === "ai" ? "ai" : "tool"} />

        {commandsResult.ok ? (
          <CommandsFeed
            title={`Command queue for ${pool.provider_name}`}
            commands={commandsResult.data
              .filter((command) => commandConcernsPool(command, pool.provider_id))
              .slice(0, 6)}
          />
        ) : null}
      </div>
    </CommandsProvider>
  );
}
