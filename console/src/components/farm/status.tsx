import { cn } from "cn";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { STATE_META, TONE, type Tone } from "@/lib/farm/state";
import type { AccountDot, EffectiveState } from "@/lib/farm/types";

/** Tinted pill used for every status in the Console: a dot, a label, a tone. */
export function ToneBadge({
  tone,
  children,
  className,
  dot = true,
  title,
}: {
  tone: Tone;
  children: React.ReactNode;
  className?: string;
  dot?: boolean;
  title?: string;
}) {
  return (
    <Badge
      variant="outline"
      title={title}
      className={cn(
        "h-5 gap-1.5 rounded-full border-transparent px-2 font-medium ring-1 ring-inset",
        TONE[tone].badge,
        className,
      )}
    >
      {dot ? <span aria-hidden="true" className={cn("size-1.5 rounded-full", TONE[tone].dot)} /> : null}
      {children}
    </Badge>
  );
}

export function StateBadge({ state, className }: { state: EffectiveState; className?: string }) {
  const meta = STATE_META[state];
  return (
    <ToneBadge tone={meta.tone} className={className} title={meta.hint}>
      {meta.label}
    </ToneBadge>
  );
}

/** One account as a colored dot with a tooltip, used on pool tiles and the request-flow map. */
export function StateDot({ state, label, size = "md" }: { state: EffectiveState; label: string; size?: "sm" | "md" }) {
  const meta = STATE_META[state];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          role="img"
          aria-label={`${label}: ${meta.label}`}
          className={cn(
            "inline-block shrink-0 rounded-full ring-1 ring-black/5 dark:ring-white/10",
            size === "sm" ? "size-2" : "size-2.5",
            TONE[meta.tone].dot,
            state === "paused" || state === "disabled" ? "opacity-70" : null,
          )}
        />
      </TooltipTrigger>
      <TooltipContent>
        {label}: {meta.label}
      </TooltipContent>
    </Tooltip>
  );
}

/** Decorative color key for legends (the meaning is in the text next to it). */
export function StateSwatch({ state, size = "md" }: { state: EffectiveState; size?: "sm" | "md" }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block shrink-0 rounded-full",
        size === "sm" ? "size-2" : "size-2.5",
        TONE[STATE_META[state].tone].dot,
      )}
    />
  );
}

/** The accounts of a pool as a row of dots, one list item each so assistive tech reads them as a list. */
export function DotList({ dots, label, size = "md" }: { dots: AccountDot[]; label: string; size?: "sm" | "md" }) {
  return (
    <ul aria-label={label} className="flex flex-wrap items-center gap-1.5">
      {dots.map((dot) => (
        <li key={dot.id} className="flex">
          <StateDot state={dot.state} label={dot.label} size={size} />
        </li>
      ))}
    </ul>
  );
}
