"use client";

import { useState } from "react";

import Link from "next/link";

import { ArrowUpRight, KeyRound, Plus } from "lucide-react";
import { toast } from "sonner";

import { DotList, ToneBadge } from "@/components/farm/status";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { STRATEGY_LABELS } from "@/lib/farm/command-meta";
import { fmtCompact, fmtPlanUsd } from "@/lib/farm/format";
import { readFirstNeedsLoginConnection } from "@/lib/farm/login-actions";
import { HEALTH_META, poolHref } from "@/lib/farm/state";
import type { PoolOverviewRow, PoolStrategy } from "@/lib/farm/types";

import { AddAccountsDialog } from "./add-accounts-dialog";
import { SignInDrawer } from "./sign-in-drawer";

function breakdown(pool: PoolOverviewRow): string[] {
  const parts = [
    { count: pool.accounts_usable, text: "usable" },
    { count: pool.accounts_paused, text: "paused" },
    { count: pool.accounts_exhausted, text: "exhausted" },
    { count: pool.accounts_needs_login, text: "need login" },
    { count: pool.accounts_open_circuit, text: "circuit open" },
    { count: pool.accounts_cooldown, text: "cooling down" },
  ];
  return parts.filter((part) => part.count > 0).map((part) => `${part.count} ${part.text}`);
}

export function PoolCard({ pool }: { pool: PoolOverviewRow }) {
  const health = HEALTH_META[pool.health];
  const strategy = pool.default_strategy ? STRATEGY_LABELS[pool.default_strategy as PoolStrategy]?.label : undefined;
  const lines = breakdown(pool);
  const isAi = pool.kind === "ai";

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerConnId, setDrawerConnId] = useState<string | null>(null);
  const [loadingSignIn, setLoadingSignIn] = useState(false);

  const handleSignInAgain = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setLoadingSignIn(true);
    try {
      const connId = await readFirstNeedsLoginConnection(pool.provider_id);
      if (connId) {
        setDrawerConnId(connId);
        setDrawerOpen(true);
      } else {
        toast.info("No accounts currently need sign-in");
      }
    } catch {
      toast.error("Failed to find account needing sign-in");
    } finally {
      setLoadingSignIn(false);
    }
  };

  return (
    <>
      <Card
        data-testid="pool-card"
        className="group relative flex h-full flex-col gap-3.5 p-4 transition-colors hover:bg-muted/40"
      >
        <Link
          href={poolHref(pool)}
          className="absolute inset-0 rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          aria-label={`View ${pool.provider_name} details`}
        />

        <div className="pointer-events-none relative z-10 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <h2 className="truncate font-semibold text-base tracking-tight">{pool.provider_name}</h2>
              <ArrowUpRight
                aria-hidden="true"
                className="size-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
              />
            </div>
            <p className="mt-0.5 text-muted-foreground text-xs">
              Executor <span className="font-mono">{pool.executor}</span> · {pool.accounts_total}{" "}
              {pool.accounts_total === 1 ? "account" : "accounts"}
            </p>
          </div>
          <ToneBadge tone={health.tone}>{health.label}</ToneBadge>
        </div>

        <div className="pointer-events-none relative z-10 space-y-1.5">
          <DotList dots={pool.account_dots} label={`${pool.provider_name} accounts`} />
          <div className="flex min-h-4 items-center justify-between text-xs">
            {isAi ? (
              <span className="font-medium text-foreground">
                {pool.accounts_usable} of {pool.accounts_total} ready
              </span>
            ) : null}
            <span className="text-muted-foreground">{lines.length > 0 ? lines.join(" · ") : "No accounts yet"}</span>
          </div>
        </div>

        <dl className="pointer-events-none relative z-10 mt-auto grid grid-cols-3 gap-3 border-t pt-3 text-xs">
          <div>
            <dt className="text-muted-foreground">Calls left</dt>
            <dd className="font-medium text-sm tabular-nums">
              {pool.unlimited ? "Unlimited" : fmtCompact(pool.remaining_calls)}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Strategy</dt>
            <dd className="truncate font-medium text-sm">{strategy ?? pool.default_strategy ?? "Default"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Plans</dt>
            <dd className="font-medium text-sm tabular-nums">
              {pool.monthly_plan_usd > 0 ? `${fmtPlanUsd(pool.monthly_plan_usd)}/mo` : "Free"}
            </dd>
          </div>
        </dl>

        {/* AI Pool Action Buttons */}
        {isAi && (
          <div className="relative z-20 flex flex-wrap items-center gap-2 border-t pt-3">
            {pool.accounts_needs_login > 0 && (
              <Button
                variant="outline"
                size="sm"
                data-testid="pool-signin-button"
                className="h-7 gap-1.5 text-xs"
                disabled={loadingSignIn}
                onClick={handleSignInAgain}
              >
                <KeyRound className="size-3" />
                Sign in again ({pool.accounts_needs_login})
              </Button>
            )}
            <AddAccountsDialog
              defaultProviderId={pool.provider_id}
              trigger={
                <Button variant="outline" size="sm" className="h-7 gap-1 text-xs" onClick={(e) => e.stopPropagation()}>
                  <Plus className="size-3" />+ Add accounts
                </Button>
              }
            />
          </div>
        )}
      </Card>

      {drawerConnId && (
        <SignInDrawer
          open={drawerOpen}
          onOpenChange={setDrawerOpen}
          connectionId={drawerConnId}
          title={`Sign in ${drawerConnId}`}
        />
      )}
    </>
  );
}
