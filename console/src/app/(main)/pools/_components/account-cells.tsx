"use client";

import { useState } from "react";

import { cn } from "cn";
import { toast } from "sonner";

import { CommandChip } from "@/components/farm/command-chip";
import { useSharedCommands } from "@/components/farm/commands-provider";
import { CopyCommand } from "@/components/farm/copy-command";
import { StateBadge, ToneBadge } from "@/components/farm/status";
import { Countdown, RelativeTime, ResetTime } from "@/components/farm/time";
import { UsageMeter } from "@/components/farm/usage-meter";
import { useLazy } from "@/components/farm/use-lazy";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { PERIOD_LABELS } from "@/lib/farm/command-meta";
import { DASH, fmtDuration, fmtInt, fmtPct, fmtPlanUsd, pluralize } from "@/lib/farm/format";
import { stateReason, TONE, type Tone } from "@/lib/farm/state";
import type { Connection } from "@/lib/farm/types";

import {
  exhaustedUnits,
  isInFlight,
  loginCommand,
  nextReset,
  optimisticStatus,
  ordinalDay,
  successRate,
} from "../_lib/account-model";

export function AccountCell({ connection }: { connection: Connection }) {
  return (
    <div className="min-w-0">
      <div className="truncate font-medium text-[13px]">{connection.label}</div>
      <div className="font-mono text-muted-foreground text-xs">{connection.id}</div>
      {connection.authRef ? (
        <div
          className="max-w-[16rem] truncate font-mono text-[11px] text-muted-foreground"
          title="A reference to where the secret lives on the PC. The secret itself is never shown here."
        >
          {connection.authRef}
        </div>
      ) : null}
    </div>
  );
}

/** Effective state badge, what is in flight for this account, and what to do when it is blocked. */
export function StatusCell({ connection, ai = false }: { connection: Connection; ai?: boolean }) {
  const { pending } = useSharedCommands();
  const tracked =
    pending[`state:${connection.id}`] ?? pending[`test:${connection.id}`] ?? pending[`remove:${connection.id}`];
  const shown = optimisticStatus(connection, pending[`state:${connection.id}`]);
  const state = shown !== connection.status ? shown : connection.effectiveState;
  const reason = shown === connection.status ? stateReason(connection) : null;

  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <StateBadge state={state} />
        <CommandChip command={tracked} />
      </div>
      {state === "needs_login" && ai ? (
        <div className="flex flex-col gap-1">
          <span className="text-muted-foreground text-xs">Sign in again on the Farm PC:</span>
          <CopyCommand command={loginCommand(connection)} />
        </div>
      ) : null}
      {reason ? (
        <p className="line-clamp-3 max-w-[11rem] whitespace-normal text-muted-foreground text-xs">{reason}</p>
      ) : null}
    </div>
  );
}

export function UsageCell({
  connection,
  max = 3,
  showReset = false,
}: {
  connection: Connection;
  max?: number;
  showReset?: boolean;
}) {
  if (connection.units.length === 0) return <span className="text-muted-foreground text-xs">No limits tracked</span>;
  const shown = connection.units.slice(0, max);
  const rest = connection.units.length - shown.length;
  return (
    <div className="flex flex-col gap-2.5">
      {shown.map((unit) => (
        <UsageMeter key={unit.unit} unit={unit.unit} used={unit.used} reserved={unit.reserved} limit={unit.limit} />
      ))}
      {rest > 0 ? <span className="text-muted-foreground text-xs">+{rest} more units</span> : null}
      {showReset ? <ResetLine connection={connection} /> : null}
    </div>
  );
}

/** One line under the meters: when the soonest limit resets. */
function ResetLine({ connection }: { connection: Connection }) {
  const reset = nextReset(connection);
  if (!reset) return <span className="text-muted-foreground text-xs">No reset</span>;
  return (
    <span className="text-muted-foreground text-xs">
      Resets <ResetTime iso={reset.nextResetAt} className="font-medium text-foreground" /> ·{" "}
      {PERIOD_LABELS[reset.period]}
    </span>
  );
}

export function ResetCell({ connection }: { connection: Connection }) {
  const reset = nextReset(connection);
  if (!reset) return <span className="text-muted-foreground text-xs">No reset</span>;
  return (
    <div className="text-xs">
      <ResetTime iso={reset.nextResetAt} className="font-medium text-[13px]" />
      <div className="text-muted-foreground">{PERIOD_LABELS[reset.period]}</div>
    </div>
  );
}

export function PlanCell({ connection }: { connection: Connection }) {
  const { plan } = connection;
  const price = plan.price_usd ?? 0;
  return (
    <div className="text-xs">
      <div className="font-medium text-[13px]">{plan.name ?? DASH}</div>
      <div className="text-muted-foreground tabular-nums">
        {price > 0 ? `${fmtPlanUsd(price)}/mo` : "Free"}
        {plan.billing_day && price > 0 ? ` · bills ${ordinalDay(plan.billing_day)}` : ""}
      </div>
    </div>
  );
}

const CIRCUIT_VIEW: Record<Connection["health"]["circuit"], { label: string; tone: Tone }> = {
  closed: { label: "Circuit closed", tone: "ok" },
  half_open: { label: "Half-open", tone: "warn" },
  open: { label: "Circuit open", tone: "bad" },
};

export function HealthCell({ connection }: { connection: Connection }) {
  const { health } = connection;
  const rate = successRate(connection);
  const cooling = connection.effectiveState === "cooldown";
  const circuit = CIRCUIT_VIEW[health.circuit];
  return (
    <div className="flex flex-col gap-1 text-xs">
      <div className="flex flex-col gap-0.5">
        <span className={cn("inline-flex items-center gap-1 font-medium", TONE[circuit.tone].text)}>
          <span aria-hidden="true" className={cn("size-1.5 rounded-full", TONE[circuit.tone].dot)} />
          {circuit.label}
        </span>
        <span className="text-muted-foreground tabular-nums">
          {rate === null ? "no calls yet" : `${fmtPct(rate, 1)} ok`}
          {health.latencyMsP50 ? ` · ${fmtDuration(health.latencyMsP50)} p50` : ""}
        </span>
      </div>
      {cooling && health.cooldownUntil ? (
        <div className="text-sky-700 dark:text-sky-400">
          Cooldown ends in <Countdown until={health.cooldownUntil} className="font-medium tabular-nums" />
        </div>
      ) : null}
      {health.lastErrorKind ? (
        <div className="text-muted-foreground">
          Last error <RelativeTime iso={health.lastErrorAt} />
          <div className="font-mono text-foreground">{health.lastErrorKind}</div>
        </div>
      ) : null}
      {health.consecutiveFailures > 0 ? (
        <div className="text-muted-foreground tabular-nums">
          {pluralize(health.consecutiveFailures, "failure")} in a row
        </div>
      ) : null}
    </div>
  );
}

/** Inline priority editor: commits on blur or Enter; Escape restores. The parent keys it on the stored value. */
export function PriorityInput({ connection }: { connection: Connection }) {
  const { pending, run } = useSharedCommands();
  const key = `prio:${connection.id}`;
  const tracked = pending[key];
  const [draft, setDraft] = useState(String(connection.priority));
  const parsed = Number(draft);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 10_000;

  function commit() {
    if (!valid) {
      setDraft(String(connection.priority));
      toast.error("Priority must be a whole number from 1 to 10,000.");
      return;
    }
    if (parsed === connection.priority || isInFlight(tracked)) return;
    void run(
      "set_priority",
      { connection_id: connection.id, priority: parsed },
      {
        key,
        label: `Set priority of ${connection.id} to ${parsed}`,
        onRejected: () => setDraft(String(connection.priority)),
      },
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Input
        type="number"
        inputMode="numeric"
        min={1}
        max={10_000}
        value={draft}
        aria-label={`Priority of ${connection.label}`}
        aria-invalid={!valid}
        title="Lower numbers are tried first"
        disabled={connection.status === "disabled"}
        className="h-7 w-16 px-2 text-right tabular-nums"
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraft(String(connection.priority));
            event.currentTarget.blur();
          }
        }}
      />
      <CommandChip command={tracked} />
    </div>
  );
}

/** On: the Farm may route to this account. Off: paused by the owner. Sends pause/resume through the queue. */
export function ActiveSwitch({ connection }: { connection: Connection }) {
  const { pending, run } = useSharedCommands();
  const key = `state:${connection.id}`;
  const tracked = pending[key];
  const status = optimisticStatus(connection, tracked);
  const checked = status !== "paused" && status !== "disabled";

  return (
    <Switch
      checked={checked}
      disabled={connection.status === "disabled" || isInFlight(tracked)}
      aria-label={`${connection.label} enabled`}
      title={checked ? "Enabled. Switch off to pause this account." : "Paused. Switch on to resume it."}
      onCheckedChange={(next) => {
        void run(
          next ? "resume" : "pause",
          { connection_id: connection.id },
          {
            key,
            label: `${next ? "Resume" : "Pause"} ${connection.id}`,
          },
        );
      }}
    />
  );
}

const loadMenu = () => import("./row-actions-menu").then((module) => module.RowActionsMenu);

/** Row actions menu. It loads right after first paint; a same-sized spacer holds its place until then. */
export function RowActions({ connection }: { connection: Connection }) {
  const Menu = useLazy(loadMenu);
  return Menu ? <Menu connection={connection} /> : <span aria-hidden="true" className="inline-block size-8" />;
}

// --- AI pool cells ------------------------------------------------------------------------------------------------

export function CliCell({ connection }: { connection: Connection }) {
  const models = connection.meta.models ?? [];
  return (
    <div className="flex min-w-[8.5rem] flex-col gap-1.5">
      <span className="inline-flex w-fit items-center rounded-md border bg-muted/50 px-1.5 py-0.5 font-mono text-[11px]">
        {connection.meta.cli ?? "cli"}
      </span>
      <div className="flex flex-wrap gap-1">
        {models.map((model) => (
          <span key={model} className="rounded border px-1.5 py-px font-mono text-[11px] text-muted-foreground">
            {model}
          </span>
        ))}
      </div>
    </div>
  );
}

/** Limit state for AI accounts: at the limit shows the time to reset as a live countdown; otherwise the meters. */
export function LimitCell({ connection }: { connection: Connection }) {
  const full = exhaustedUnits(connection);
  const resetAt = full
    .map((unit) => unit.nextResetAt)
    .filter((value): value is string => Boolean(value))
    .sort()[0];
  return (
    <div className="flex flex-col gap-2.5">
      {full.length > 0 ? (
        <div className="flex flex-col gap-0.5">
          <ToneBadge tone="warn" className="w-fit">
            Limit reached
          </ToneBadge>
          {resetAt ? (
            <span className="text-muted-foreground text-xs">
              Resets in <Countdown until={resetAt} className="font-medium text-foreground tabular-nums" />
            </span>
          ) : (
            <span className="text-muted-foreground text-xs">Reset time not reported yet</span>
          )}
        </div>
      ) : null}
      {connection.effectiveState === "cooldown" && connection.health.cooldownUntil ? (
        <span className="text-sky-700 text-xs dark:text-sky-400">
          Cooling down for <Countdown until={connection.health.cooldownUntil} className="font-medium tabular-nums" />
        </span>
      ) : null}
      <UsageCell connection={connection} max={2} />
    </div>
  );
}

export function CallsCell({ connection }: { connection: Connection }) {
  return (
    <div className="text-right text-xs tabular-nums">
      <div className="font-medium text-[13px]">{fmtInt(connection.callsToday)}</div>
      <div className="text-muted-foreground">{fmtInt(connection.sessionsCount)} sessions</div>
    </div>
  );
}
