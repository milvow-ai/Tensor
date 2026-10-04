import { cn } from "cn";

import { fmtInt, fmtPct, fmtQty } from "@/lib/farm/format";
import { TONE, type Tone } from "@/lib/farm/state";

function toneFor(share: number): Tone {
  if (share >= 0.9) return "bad";
  if (share >= 0.75) return "warn";
  return "ok";
}

/**
 * Used / limit for one consumption unit. Used is the solid segment, reserved (held by in-flight runs) is hatched.
 * Colors move from green to amber at 75% and red at 90%. An unlimited unit shows its count without a bar.
 * Screen readers get a native <meter>; the painted bar is decoration.
 */
export function UsageMeter({
  unit,
  used,
  reserved,
  limit,
  className,
  showUnit = true,
}: {
  unit: string;
  used: number;
  reserved: number;
  limit: number | null;
  className?: string;
  showUnit?: boolean;
}) {
  if (limit === null || limit <= 0) {
    return (
      <div className={cn("min-w-[9.5rem]", className)}>
        <div className="flex items-baseline justify-between gap-2 text-xs">
          <span className="font-medium tabular-nums">{showUnit ? fmtQty(used, unit) : fmtInt(used)}</span>
          <span className="text-muted-foreground">unlimited</span>
        </div>
        <div aria-hidden="true" className="mt-1.5 h-1.5 rounded-full border border-border border-dashed" />
      </div>
    );
  }

  const usedShare = Math.min(used / limit, 1);
  const reservedShare = Math.min(reserved / limit, 1 - usedShare);
  const tone = toneFor((used + reserved) / limit);

  return (
    <div className={cn("min-w-[9.5rem]", className)}>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-medium tabular-nums">
          {fmtInt(used)}
          <span className="font-normal text-muted-foreground"> / {fmtInt(limit)}</span>
          {showUnit ? <span className="font-normal text-muted-foreground"> {unit}</span> : null}
        </span>
        <span className={cn("tabular-nums", tone === "ok" ? "text-muted-foreground" : TONE[tone].text)}>
          {fmtPct(used / limit)}
        </span>
      </div>
      <meter
        className="sr-only"
        aria-label={`${unit} used`}
        min={0}
        max={limit}
        value={Math.min(used, limit)}
        title={`${fmtInt(used)} of ${fmtInt(limit)} ${unit} used${reserved > 0 ? `, ${fmtInt(reserved)} reserved` : ""}`}
      />
      <div aria-hidden="true" className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-muted">
        <div className={cn("h-full", TONE[tone].bar)} style={{ width: `${usedShare * 100}%` }} />
        {reservedShare > 0 ? (
          <div
            className={cn("h-full opacity-55", TONE[tone].text)}
            style={{
              width: `${reservedShare * 100}%`,
              backgroundImage:
                "repeating-linear-gradient(135deg, currentColor 0, currentColor 2px, transparent 2px, transparent 4px)",
            }}
          />
        ) : null}
      </div>
      {reserved > 0 ? (
        <div className="mt-1 text-[11px] text-muted-foreground tabular-nums">{fmtInt(reserved)} reserved</div>
      ) : null}
    </div>
  );
}
