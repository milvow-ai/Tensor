import type { TrackedCommand } from "@/components/farm/use-command";
import type { Connection, ConnectionStatus, UnitUsage } from "@/lib/farm/types";

/** True while a command for this row is still in flight (or just finished and the table is about to refresh). */
export function isInFlight(tracked: TrackedCommand | undefined): boolean {
  return tracked?.phase === "queued" || tracked?.phase === "running";
}

/**
 * The status to show while a pause or resume is queued: the owner's intent, shown at once. A rejected command is not
 * applied, so the row falls back to what the Farm reports.
 */
export function optimisticStatus(connection: Connection, tracked: TrackedCommand | undefined): ConnectionStatus {
  if (!tracked || tracked.phase === "rejected" || tracked.phase === "failed" || tracked.phase === "timeout") {
    return connection.status;
  }
  if (tracked.kind === "pause") return "paused";
  if (tracked.kind === "resume" && connection.status === "paused") return "active";
  return connection.status;
}

/** Share of successful calls, or null before the first call. */
export function successRate(connection: Connection): number | null {
  const { successCount, failureCount } = connection.health;
  const total = successCount + failureCount;
  return total === 0 ? null : successCount / total;
}

/** The soonest upcoming reset among an account's units, with its unit and period. */
export function nextReset(connection: Connection): Pick<UnitUsage, "nextResetAt" | "unit" | "period"> | null {
  let best: UnitUsage | null = null;
  for (const unit of connection.units) {
    if (!unit.nextResetAt) continue;
    if (!best || unit.nextResetAt < (best.nextResetAt ?? "")) best = unit;
  }
  return best ? { nextResetAt: best.nextResetAt, unit: best.unit, period: best.period } : null;
}

/** Units that are at their limit right now. */
export function exhaustedUnits(connection: Connection): UnitUsage[] {
  return connection.units.filter((unit) => unit.limit !== null && unit.used + unit.reserved >= unit.limit);
}

export function ordinalDay(day: number | undefined): string {
  if (!day) return "";
  const rest = day % 100;
  if (rest >= 11 && rest <= 13) return `${day}th`;
  return `${day}${({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[day % 10] ?? "th"}`;
}

/** The command a person runs on the Farm PC to sign an AI account in again. */
export function loginCommand(connection: Connection): string {
  return `farm ai login ${connection.id}`;
}
