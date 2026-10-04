"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { toast } from "sonner";

import { readCommand, submitCommand } from "@/lib/farm/actions";
import { COMMAND_LABELS, commandTarget } from "@/lib/farm/command-meta";
import type { CommandKind, CommandStatus, JsonObject } from "@/lib/farm/types";

export type TrackedPhase = CommandStatus | "timeout";

export interface TrackedCommand {
  id: string;
  kind: CommandKind;
  phase: TrackedPhase;
  reason?: string;
  /** What the command is about, for rows that do not exist yet (an account being added). */
  subject?: string;
}

export interface RunOptions {
  /** Key of the thing the command acts on (account id, pool id, alert id), used to show per-row state. */
  key: string;
  /** Short human text, e.g. "Pause clay-05". Defaults to the command label and its target. */
  label?: string;
  /** Display name of the thing being created, shown on a placeholder row until the Farm adds it. */
  subject?: string;
  /** Called after the Farm reported `done`. */
  onDone?: () => void;
  /** Called when the Farm rejected the command or it failed, so the caller can revert optimistic UI. */
  onRejected?: (reason: string) => void;
}

const POLL_MS = 600;
const GIVE_UP_MS = 90_000;
const SETTLE_MS = 1400;

/**
 * Sends Console commands through `farm_commands` and follows each one: queued, running, then done or rejected.
 * `pending[key]` drives optimistic row state; toasts narrate the same transitions; a server refresh runs when the
 * command settles so the table shows what the Farm actually did.
 */
export function useCommands() {
  const router = useRouter();
  const [pending, setPending] = useState<Record<string, TrackedCommand>>({});
  const alive = useRef<boolean>(true);
  const isAlive = useCallback(() => alive.current, []);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const track = useCallback(
    (key: string, value: TrackedCommand | null) => {
      if (!isAlive()) return;
      setPending((current) => {
        const next = { ...current };
        if (value) next[key] = value;
        else delete next[key];
        return next;
      });
    },
    [isAlive],
  );

  /** Follows a queued command to its end, narrating each step in the toast and the row chip. */
  const follow = useCallback(
    async (id: string, kind: CommandKind, label: string, toastId: string | number, options: RunOptions) => {
      const startedAt = Date.now();
      while (isAlive()) {
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        if (!isAlive()) return;
        const polled = await readCommand(id);
        const timedOut = Date.now() - startedAt > GIVE_UP_MS;
        if (!polled.ok || !polled.data) {
          if (timedOut) break;
          continue;
        }
        const status = polled.data.status;
        if (status === "queued" || status === "running") {
          track(options.key, { id, kind, phase: status, subject: options.subject });
          toast.loading(`${label}: ${status}`, { id: toastId });
          if (timedOut) break;
          continue;
        }
        if (status === "done") {
          track(options.key, { id, kind, phase: "done", subject: options.subject });
          toast.success(`${label}: done`, { id: toastId });
          options.onDone?.();
          router.refresh();
          setTimeout(() => track(options.key, null), SETTLE_MS);
          return;
        }
        const reason = String(
          (polled.data.result as { reason?: unknown } | null)?.reason ?? "The Farm could not run this command.",
        );
        track(options.key, { id, kind, phase: status, reason, subject: options.subject });
        toast.error(`${label}: ${status}`, { id: toastId, description: reason });
        options.onRejected?.(reason);
        router.refresh();
        setTimeout(() => track(options.key, null), SETTLE_MS * 2);
        return;
      }
      if (isAlive()) {
        toast.warning(`${label}: still queued`, {
          id: toastId,
          description: "The Farm has not picked this up yet. Is it running on the PC?",
        });
        track(options.key, { id, kind, phase: "timeout", subject: options.subject });
      }
    },
    [router, track, isAlive],
  );

  /**
   * Validates and queues a command. Resolves true as soon as it is queued (false when it was refused before queueing);
   * the follow-up to done or rejected continues in the background and reports through `onDone` / `onRejected`.
   */
  const run = useCallback(
    async (kind: CommandKind, payload: JsonObject, options: RunOptions): Promise<boolean> => {
      const label = options.label ?? `${COMMAND_LABELS[kind]} ${commandTarget(kind, payload)}`;
      const toastId = toast.loading(`${label}: queued`);
      track(options.key, { id: "", kind, phase: "queued", subject: options.subject });

      const sent = await submitCommand(kind, payload);
      if (!sent.ok) {
        toast.error(`${label}: not sent`, { id: toastId, description: sent.error });
        track(options.key, null);
        options.onRejected?.(sent.error);
        return false;
      }
      track(options.key, { id: sent.data.id, kind, phase: sent.data.status, subject: options.subject });
      router.refresh();
      void follow(sent.data.id, kind, label, toastId, options);
      return true;
    },
    [follow, router, track],
  );

  return { pending, run };
}

/** Re-runs the server components of the current page on an interval (only while the tab is visible). */
export function useLiveRefresh(intervalMs: number) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") router.refresh();
    }, intervalMs);
    return () => clearInterval(id);
  }, [router, intervalMs]);
}
