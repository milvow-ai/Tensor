"use client";

import { useState } from "react";

import { cn } from "cn";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";

/** A shell command the owner runs on the Farm PC, with a copy button. Shown for needs-login states. */
export function CopyCommand({ command, className }: { command: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      toast.success("Command copied");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Could not copy. Select the command and copy it by hand.");
    }
  }

  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-md border bg-muted/60 py-0.5 pr-0.5 pl-2",
        className,
      )}
    >
      <code className="truncate font-mono text-[12px]">{command}</code>
      <button
        type="button"
        onClick={copy}
        aria-label={`Copy command ${command}`}
        className="inline-flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-background hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        {copied ? (
          <Check aria-hidden="true" className="size-3.5 text-emerald-600" />
        ) : (
          <Copy aria-hidden="true" className="size-3.5" />
        )}
      </button>
    </span>
  );
}
