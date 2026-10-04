"use client";

import { useEffect, useState } from "react";

import Link from "next/link";

import { ExternalLink, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { fetchRunDetail } from "@/lib/farm/actions";
import type { RunDetailRow } from "@/lib/farm/types";

import { RunDetailView } from "./run-detail-view";

export function RunDetailDrawer({
  runId,
  open,
  onOpenChange,
}: {
  runId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [detail, setDetail] = useState<RunDetailRow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !runId) {
      setDetail(null);
      setError(null);
      return;
    }

    let active = true;
    setLoading(true);
    setError(null);

    void fetchRunDetail(runId).then((res) => {
      if (!active) return;
      setLoading(false);
      if (res.ok) {
        setDetail(res.data);
      } else {
        setError(res.error);
      }
    });

    return () => {
      active = false;
    };
  }, [open, runId]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-2xl overflow-y-auto p-4 sm:p-6">
        <SheetHeader className="pb-2">
          <div className="flex items-center justify-between pr-6">
            <SheetTitle className="text-base font-mono">Run {runId ? runId.slice(0, 8) : ""}</SheetTitle>
            {runId ? (
              <Button size="sm" variant="ghost" className="h-7 text-xs gap-1.5" asChild>
                <Link href={`/runs/${runId}`}>
                  <span>Full page</span>
                  <ExternalLink className="size-3" />
                </Link>
              </Button>
            ) : null}
          </div>
          <SheetDescription>Live trajectory, ranking reasons, fallbacks, and step decisions.</SheetDescription>
        </SheetHeader>

        <div className="mt-4">
          {loading && (
            <div className="flex h-64 flex-col items-center justify-center gap-2 text-muted-foreground text-xs">
              <Loader2 className="size-6 animate-spin" />
              <span>Loading run trajectory...</span>
            </div>
          )}
          {!loading && error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-destructive text-xs">
              {error}
            </div>
          )}
          {!loading && !error && detail && <RunDetailView run={detail} />}
          {!loading && !error && !detail && (
            <div className="py-12 text-center text-muted-foreground text-xs">Run not found.</div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
