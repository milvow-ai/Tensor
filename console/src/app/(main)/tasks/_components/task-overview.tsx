import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fmtAbsolute, fmtInt } from "@/lib/farm/format";
import { accountIdentity } from "@/lib/farm/identity";
import type { AllowanceWindow, HandoffRow, WorkerRow } from "@/lib/farm/task-data";
import type { Connection } from "@/lib/farm/types";

function taskLabel(task: HandoffRow, now: number): string {
  if (task.state === "done") return "Complete";
  if (task.state === "paused" || Date.parse(task.lease_until) <= now) return "Needs operator";
  return task.approach === "finish" ? "Finishing" : "Delegating";
}

function Allowance({ row, now }: { row: WorkerRow; now: number }) {
  const windows = Object.entries(row.allowance_windows ?? {}).filter(
    (entry): entry is [string, AllowanceWindow] =>
      (entry[0] === "primary" || entry[0] === "secondary") && typeof entry[1]?.usedPercent === "number",
  );
  if (windows.length === 0) {
    return (
      <p className="text-muted-foreground">
        Provider quota {row.allowance_status === "unsupported" ? "unavailable" : "unobserved"}
      </p>
    );
  }
  const age = now - Date.parse(row.allowance_observed_at ?? "");
  const stale = row.allowance_status !== "ok" || !(age >= 0 && age <= 2_000 * row.allowance_poll_seconds);
  return (
    <ul className="space-y-1">
      {windows.map(([name, window]) => {
        const reset = window.resetsAt ? new Date(window.resetsAt * 1_000) : null;
        const duration = window.windowDurationMins ? `${fmtInt(window.windowDurationMins)}-min` : name;
        return (
          <li key={name}>
            {fmtInt(window.usedPercent ?? 0)}% used · {duration} window
            {reset ? ` · resets ${fmtAbsolute(reset.toISOString())}` : ""}
            {stale || (reset && reset.getTime() <= now) ? " · stale" : ""}
          </li>
        );
      })}
    </ul>
  );
}

export function TaskOverview({
  tasks,
  workers,
  accounts,
  now,
}: {
  tasks: HandoffRow[];
  workers: WorkerRow[];
  accounts: Connection[];
  now: number;
}) {
  const activity = new Map(workers.map((worker) => [worker.account, worker]));
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
      <Card>
        <CardHeader>
          <CardTitle>Project handoffs</CardTitle>
          <CardDescription>
            Resume a task from your IDE with <code>task_list</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {tasks.length === 0 ? (
            <p className="text-muted-foreground text-sm">No tracked project tasks.</p>
          ) : (
            <ul className="divide-y">
              {tasks.map((task) => (
                <li key={task.id} className="flex flex-wrap items-start justify-between gap-2 py-3">
                  <div className="min-w-0 space-y-1">
                    <p className="break-words font-medium text-sm">{task.title}</p>
                    <p className="break-words text-muted-foreground text-xs">
                      {task.project} · {task.operator_client}
                    </p>
                    <code className="break-all text-muted-foreground text-xs">{task.id}</code>
                  </div>
                  <Badge variant="outline">{taskLabel(task, now)}</Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Worker activity</CardTitle>
          <CardDescription>Account slots, queued jobs, active models, and quota.</CardDescription>
        </CardHeader>
        <CardContent>
          {accounts.length === 0 ? (
            <p className="text-muted-foreground text-sm">No AI worker accounts connected.</p>
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {accounts.map((account) => {
                const row = activity.get(account.id);
                const identity = accountIdentity(account.meta);
                return (
                  <li key={account.id} className="min-w-0 space-y-2 rounded-lg border p-3 text-sm">
                    <div>
                      <p className="break-words font-medium">{identity?.name ?? account.label}</p>
                      <p className="break-words text-muted-foreground text-xs">
                        {account.providerName} · {identity?.email ?? account.id}
                      </p>
                    </div>
                    <p>
                      {row
                        ? `${fmtInt(row.busy)} / ${fmtInt(row.slots)} slots busy · ${fmtInt(row.queued)} queued`
                        : "Activity unavailable"}
                    </p>
                    {row && row.running_models.length > 0 && (
                      <p className="break-words text-muted-foreground">
                        Requested{" "}
                        {row.running_models
                          .map((model) => `${model.model ?? "default"} (${fmtInt(model.jobs)})`)
                          .join(", ")}
                      </p>
                    )}
                    <div className="space-y-1 text-xs">
                      {account.units
                        .filter((unit) => unit.remaining !== null)
                        .map((unit) => (
                          <p key={unit.unit}>
                            {fmtInt(unit.remaining ?? 0)} {unit.unit} left
                            {unit.nextResetAt ? ` · resets ${fmtAbsolute(unit.nextResetAt)}` : ""}
                          </p>
                        ))}
                      {row && <Allowance row={row} now={now} />}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
