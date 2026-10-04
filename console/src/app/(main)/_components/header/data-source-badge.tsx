import { cn } from "cn";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { TONE } from "@/lib/farm/state";
import type { DataSourceKind } from "@/lib/farm/types";

/** Always-visible reminder of where the numbers come from, so fixture data is never mistaken for the real Farm. */
export function DataSourceBadge({ mode }: { mode: DataSourceKind }) {
  const fixtures = mode === "fixtures";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="data-source"
          className={cn(
            "hidden h-6 items-center gap-1.5 rounded-full px-2.5 font-medium text-xs ring-1 ring-inset sm:inline-flex",
            fixtures ? TONE.warn.badge : TONE.ok.badge,
          )}
        >
          <span aria-hidden="true" className={cn("size-1.5 rounded-full", fixtures ? TONE.warn.dot : TONE.ok.dot)} />
          {fixtures ? "Fixtures" : "Live"}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {fixtures
          ? "Demo data from fixtures. Nothing here is the real Farm. Development only."
          : "Reading the Farm's Supabase database. Commands go through the farm_commands queue."}
      </TooltipContent>
    </Tooltip>
  );
}
