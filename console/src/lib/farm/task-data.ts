import { createClient } from "@/lib/supabase/server";

import { getFarmData } from "./data";
import { getTenantId } from "./seal";

export interface HandoffRow {
  id: string;
  title: string;
  project: string;
  state: "active" | "paused" | "done";
  operator_client: string;
  approach: "finish" | "delegate";
  lease_until: string;
}

export interface AllowanceWindow {
  usedPercent?: number;
  resetsAt?: number;
  windowDurationMins?: number;
}

export interface WorkerRow {
  account: string;
  slots: number;
  busy: number;
  queued: number;
  running_models: { model: string | null; jobs: number }[];
  allowance_status: string | null;
  allowance_observed_at: string | null;
  allowance_poll_seconds: number;
  allowance_windows: { primary?: AllowanceWindow; secondary?: AllowanceWindow } | null;
}

export async function getTaskOverview(): Promise<{ tasks: HandoffRow[]; workers: WorkerRow[] }> {
  const data = await getFarmData();
  const { FixturesFarmData } = await import("./fixtures/source");
  if (data instanceof FixturesFarmData) return { tasks: [], workers: [] };

  const db = await createClient();
  const tenantId = getTenantId();
  const [tasks, workers] = await Promise.all([
    db
      .from("farm_tasks")
      .select("id,title,project,state,operator_client,approach,lease_until")
      .eq("tenant_id", tenantId)
      .order("updated_at", { ascending: false })
      .limit(30)
      .returns<HandoffRow[]>(),
    db
      .from("v_worker_activity")
      .select(
        "account,slots,busy,queued,running_models,allowance_status,allowance_observed_at,allowance_windows,allowance_poll_seconds",
      )
      .eq("tenant_id", tenantId)
      .eq("kind", "ai")
      .order("account")
      .returns<WorkerRow[]>(),
  ]);
  if (tasks.error) throw new Error(`Could not read task handoffs: ${tasks.error.message}`);
  if (workers.error) throw new Error(`Could not read worker activity: ${workers.error.message}`);
  return { tasks: tasks.data ?? [], workers: workers.data ?? [] };
}
