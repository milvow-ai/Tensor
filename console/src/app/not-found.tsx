import Link from "next/link";

import { cn } from "cn";
import { Compass } from "lucide-react";

import { FarmMark } from "@/components/farm/brand";
import { buttonVariants } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center gap-4 px-6 text-center">
      <FarmMark className="size-10 text-sm" />
      <div className="flex size-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Compass aria-hidden="true" className="size-5" />
      </div>
      <h1 className="font-semibold text-xl tracking-tight">This page does not exist</h1>
      <p className="max-w-sm text-muted-foreground text-sm">
        The link may be old, or the pool or run it pointed to was removed.
      </p>
      <Link href="/overview" className={cn(buttonVariants({ variant: "default" }))}>
        Back to Overview
      </Link>
    </main>
  );
}
