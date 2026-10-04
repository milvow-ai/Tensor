"use client";

import { useEffect } from "react";

import { RotateCcw } from "lucide-react";

import { ErrorState } from "@/components/farm/states";
import { Button } from "@/components/ui/button";

/** Last-resort boundary for a page that crashed while rendering. Expected data failures are handled in the pages. */
export default function ErrorBoundary({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto max-w-xl pt-10">
      <ErrorState
        title="This page failed to render"
        message={`Something went wrong while building this page. Nothing was changed. ${error.digest ? `Reference: ${error.digest}.` : ""}`}
      >
        <Button variant="outline" size="sm" onClick={reset}>
          <RotateCcw data-icon="inline-start" aria-hidden="true" />
          Try again
        </Button>
      </ErrorState>
    </div>
  );
}
