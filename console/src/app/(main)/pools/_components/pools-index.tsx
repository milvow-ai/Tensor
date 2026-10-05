import { Bot, Wrench } from "lucide-react";
import Link from "next/link";

import { CommandsProvider } from "@/components/farm/commands-provider";
import { PageHeader } from "@/components/farm/page-header";
import { EmptyState, ErrorState } from "@/components/farm/states";
import { Button } from "@/components/ui/button";
import { attempt } from "@/lib/farm/data";
import { fmtInt, fmtPlanUsd } from "@/lib/farm/format";
import type { ProviderKind } from "@/lib/farm/types";

import { AddAiAccountDialog } from "./add-ai-account-dialog";
import { PoolCard } from "./pool-card";

const COPY = {
  tool: {
    title: "Tools & Pools",
    description: "Every tool pool (Clay, Reoon, ZeroBounce, Apollo, Hunter) with its accounts, limits and health.",
    icon: Wrench,
    empty: "No tool pools are registered yet.",
    emptyHint:
      "Add an MCP server or an OpenAPI provider from Integrations (or with `farm mcp add`); its account pool appears here.",
  },
  ai: {
    title: "AI Pools",
    description:
      "Claude, Codex, Gemini and Hermes accounts that other AIs can delegate work to, each with its own login and limits.",
    icon: Bot,
    empty: "No AI pools are registered yet.",
    emptyHint:
      "Add a Claude, Codex, Gemini or Hermes account from Integrations (or with `farm ai add`); its pool appears here.",
  },
} as const;

export async function PoolsIndex({ kind }: { kind: ProviderKind }) {
  const copy = COPY[kind];
  const result = await attempt((data) => data.listPools(kind));

  if (!result.ok) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title={copy.title} description={copy.description} />
        <ErrorState message={result.message} />
      </div>
    );
  }

  const pools = result.data;
  const accounts = pools.reduce((sum, pool) => sum + pool.accounts_total, 0);
  const usable = pools.reduce((sum, pool) => sum + pool.accounts_usable, 0);
  const plans = pools.reduce((sum, pool) => sum + pool.monthly_plan_usd, 0);

  return (
    <CommandsProvider>
      <div className="flex flex-col gap-4 md:gap-5">
        <PageHeader
          title={copy.title}
          description={copy.description}
          actions={kind === "ai" ? <AddAiAccountDialog /> : null}
        />
        {pools.length === 0 ? (
          <EmptyState icon={copy.icon} title={copy.empty} description={copy.emptyHint}>
            <Button asChild>
              <Link href="/integrations">Add your first MCP server</Link>
            </Button>
          </EmptyState>
        ) : (
          <>
            <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm" aria-label="Totals">
              <div className="flex items-baseline gap-1.5">
                <dt className="text-muted-foreground">Pools</dt>
                <dd className="font-semibold tabular-nums">{fmtInt(pools.length)}</dd>
              </div>
              <div className="flex items-baseline gap-1.5">
                <dt className="text-muted-foreground">Accounts usable</dt>
                <dd className="font-semibold tabular-nums">
                  {fmtInt(usable)} / {fmtInt(accounts)}
                </dd>
              </div>
              <div className="flex items-baseline gap-1.5">
                <dt className="text-muted-foreground">Plans per month</dt>
                <dd className="font-semibold tabular-nums">{fmtPlanUsd(plans)}</dd>
              </div>
            </dl>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {pools.map((pool) => (
                <PoolCard key={pool.provider_id} pool={pool} />
              ))}
            </div>
          </>
        )}
      </div>
    </CommandsProvider>
  );
}
