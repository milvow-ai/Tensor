import { Terminal } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { COMMAND_LABELS, commandTarget } from "@/lib/farm/command-meta";
import { COMMAND_STATUS_META } from "@/lib/farm/state";
import type { FarmCommand } from "@/lib/farm/types";

import { SectionTitle } from "./section-title";
import { EmptyState } from "./states";
import { ToneBadge } from "./status";
import { RelativeTime } from "./time";

/** The last commands the Console sent to the Farm and what became of them. */
export function CommandsFeed({
  commands,
  className,
  title = "Command queue",
}: {
  commands: FarmCommand[];
  className?: string;
  title?: string;
}) {
  return (
    <Card className={className} data-testid="commands-feed">
      <CardHeader>
        <SectionTitle>{title}</SectionTitle>
        <CardDescription>
          What the Console asked the Farm to do. The Farm runs each one and reports back.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {commands.length === 0 ? (
          <EmptyState
            icon={Terminal}
            title="No commands yet"
            description="Pausing an account, changing a strategy or adding an account shows up here."
          />
        ) : (
          <ul className="divide-y">
            {commands.map((command) => {
              const meta = COMMAND_STATUS_META[command.status];
              const reason = (command.result as { reason?: unknown } | null)?.reason;
              return (
                <li
                  key={command.id}
                  className="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0"
                  data-testid="command-row"
                  data-kind={command.kind}
                >
                  <div className="min-w-0">
                    <div className="truncate font-medium text-[13px]">
                      {COMMAND_LABELS[command.kind] ?? command.kind}{" "}
                      <span className="font-mono text-muted-foreground">
                        {commandTarget(command.kind, command.payload)}
                      </span>
                    </div>
                    <div className="text-muted-foreground text-xs">
                      {command.created_by ?? "console"} · <RelativeTime iso={command.created_at} />
                    </div>
                    {typeof reason === "string" ? (
                      <div className="mt-0.5 text-amber-800 text-xs dark:text-amber-400">{reason}</div>
                    ) : null}
                  </div>
                  <ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
