import { cn } from "cn";
import { Check, Clock, Loader2, TriangleAlert } from "lucide-react";

import type { TrackedCommand } from "./use-command";

const LABEL: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  rejected: "Rejected",
  failed: "Failed",
  timeout: "Waiting for Farm",
};

const ACTIVE = "text-sky-700 dark:text-sky-400";
const PROBLEM = "text-amber-800 dark:text-amber-400";
const LOOK: Record<TrackedCommand["phase"], { Icon: typeof Check; tone: string }> = {
  queued: { Icon: Clock, tone: ACTIVE },
  running: { Icon: Loader2, tone: ACTIVE },
  done: { Icon: Check, tone: "text-emerald-700 dark:text-emerald-400" },
  rejected: { Icon: TriangleAlert, tone: PROBLEM },
  failed: { Icon: TriangleAlert, tone: PROBLEM },
  timeout: { Icon: TriangleAlert, tone: PROBLEM },
};

/** Per-row feedback for a command in flight: Queued, Running, Done or Rejected. */
export function CommandChip({ command, className }: { command: TrackedCommand | undefined; className?: string }) {
  if (!command) return null;
  const { phase } = command;
  const { Icon, tone } = LOOK[phase];
  return (
    <span
      role="status"
      data-testid="command-chip"
      data-phase={phase}
      title={command.reason}
      className={cn("inline-flex items-center gap-1 font-medium text-xs", tone, className)}
    >
      <Icon aria-hidden="true" className={cn("size-3", phase === "running" ? "animate-spin" : null)} />
      {LABEL[phase]}
    </span>
  );
}
