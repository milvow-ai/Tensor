import { cn } from "cn";

/** The Harness Farm text mark: "HF" on a green tile. Same shape as the favicon (src/app/icon.svg). */
export function FarmMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex size-8 shrink-0 select-none items-center justify-center rounded-lg bg-emerald-700 font-bold text-[13px] text-white tracking-tight shadow-xs ring-1 ring-white/10",
        className,
      )}
    >
      HF
    </span>
  );
}
