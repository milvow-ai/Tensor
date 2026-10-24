import type { FarmStatus } from "@/components/kit";
import { RUN_STATUS_META } from "@/lib/farm/state";
import type { RunStatus } from "@/lib/farm/types";

/** Kit status chip for a run: the Farm's own labels, mapped onto the kit's status palette. */
export const RUN_CHIP: Record<RunStatus, { status: FarmStatus; label: string }> = {
  succeeded: { status: "done", label: RUN_STATUS_META.succeeded.label },
  failed: { status: "failed", label: RUN_STATUS_META.failed.label },
  blocked: { status: "limited", label: RUN_STATUS_META.blocked.label },
  running: { status: "running", label: RUN_STATUS_META.running.label },
};

/** Surface of a kit card, shared by the Activity list and the trace view. */
export const KIT_CARD =
  "rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22]";

export const KIT_TEXT_PRIMARY = "text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]";
export const KIT_TEXT_SECONDARY = "text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]";
