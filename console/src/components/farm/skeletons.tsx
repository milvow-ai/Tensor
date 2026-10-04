import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const FOUR = ["a", "b", "c", "d"] as const;
const SIX = ["a", "b", "c", "d", "e", "f"] as const;
const FIVE = ["a", "b", "c", "d", "e"] as const;

export function HeaderSkeleton() {
  return (
    <div className="space-y-2" aria-hidden="true">
      <Skeleton className="h-3 w-40" />
      <Skeleton className="h-7 w-56" />
      <Skeleton className="h-4 w-full max-w-lg" />
    </div>
  );
}

export function KpiRowSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-hidden="true">
      {FOUR.slice(0, count).map((key) => (
        <Card key={key} size="sm" className="gap-3 px-4">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-8 w-28" />
          <Skeleton className="h-3 w-full" />
        </Card>
      ))}
    </div>
  );
}

export function CardSkeleton({ className = "h-64" }: { className?: string }) {
  return (
    <Card className="gap-4 px-4" aria-hidden="true">
      <Skeleton className="h-4 w-44" />
      <Skeleton className={`w-full ${className}`} />
    </Card>
  );
}

export function PageLoading({ label }: { label: string }) {
  return (
    <div className="flex flex-col gap-4 md:gap-5" role="status" aria-label={label}>
      <HeaderSkeleton />
      <KpiRowSkeleton />
      <div className="grid gap-4 md:gap-5 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <CardSkeleton />
        </div>
        <div className="xl:col-span-2">
          <CardSkeleton />
        </div>
      </div>
    </div>
  );
}

export function PoolGridSkeleton() {
  return (
    <div className="flex flex-col gap-4 md:gap-5" role="status" aria-label="Loading pools">
      <HeaderSkeleton />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3" aria-hidden="true">
        {SIX.map((key) => (
          <Card key={key} className="gap-4 px-4">
            <div className="flex justify-between">
              <Skeleton className="h-5 w-28" />
              <Skeleton className="h-5 w-16 rounded-full" />
            </div>
            <Skeleton className="h-3 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
            <Skeleton className="h-10 w-full" />
          </Card>
        ))}
      </div>
    </div>
  );
}

export function PoolDetailSkeleton() {
  return (
    <div className="flex flex-col gap-4 md:gap-5" role="status" aria-label="Loading pool">
      <HeaderSkeleton />
      <KpiRowSkeleton />
      <Card className="gap-0 py-0" aria-hidden="true">
        <Skeleton className="h-9 w-full rounded-none rounded-t-xl" />
        {FIVE.map((key) => (
          <div key={key} className="flex items-center gap-4 border-t px-4 py-4">
            <Skeleton className="h-9 w-40" />
            <Skeleton className="h-5 w-20 rounded-full" />
            <Skeleton className="h-8 w-44" />
            <Skeleton className="hidden h-8 w-28 lg:block" />
            <Skeleton className="ml-auto h-5 w-9 rounded-full" />
          </div>
        ))}
      </Card>
    </div>
  );
}
