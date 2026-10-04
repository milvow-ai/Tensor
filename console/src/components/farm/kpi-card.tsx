import { cn } from "cn";
import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";

/** One headline number: label, value, optional suffix and a free-form detail area beneath. */
export function KpiCard({
  label,
  icon: Icon,
  value,
  suffix,
  children,
  className,
}: {
  label: string;
  icon: LucideIcon;
  value: React.ReactNode;
  suffix?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <Card size="sm" className={cn("gap-2 px-4", className)}>
      <div className="flex items-center justify-between gap-2 text-muted-foreground text-xs">
        <span className="font-medium">{label}</span>
        <Icon aria-hidden="true" className="size-4" />
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="font-semibold text-2xl tabular-nums tracking-tight">{value}</span>
        {suffix ? <span className="text-muted-foreground text-sm tabular-nums">{suffix}</span> : null}
      </div>
      {children ? <div className="text-xs">{children}</div> : null}
    </Card>
  );
}
