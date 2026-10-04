"use client";

import { useState } from "react";

import { Copy, EllipsisVertical, FlaskConical, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { useSharedCommands } from "@/components/farm/commands-provider";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Connection } from "@/lib/farm/types";

export function RowActionsMenu({ connection }: { connection: Connection }) {
  const { run } = useSharedCommands();
  const [confirming, setConfirming] = useState(false);

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${what} copied`);
    } catch {
      toast.error("Could not copy to the clipboard.");
    }
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8 text-muted-foreground"
            aria-label={`Actions for ${connection.label}`}
          >
            <EllipsisVertical aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuItem
            onSelect={() => {
              void run(
                "test_connection",
                { connection_id: connection.id },
                { key: `test:${connection.id}`, label: `Test ${connection.id}` },
              );
            }}
          >
            <FlaskConical aria-hidden="true" />
            Test connection
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void copy(connection.id, "Account id")}>
            <Copy aria-hidden="true" />
            Copy account id
          </DropdownMenuItem>
          {connection.authRef ? (
            <DropdownMenuItem onSelect={() => void copy(connection.authRef ?? "", "Auth reference")}>
              <Copy aria-hidden="true" />
              Copy auth reference
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => setConfirming(true)}>
            <Trash2 aria-hidden="true" />
            Remove account
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {connection.label}?</AlertDialogTitle>
            <AlertDialogDescription>
              The Farm stops routing to <span className="font-mono">{connection.id}</span> and removes it from the
              registry. Past runs and billing history stay. The secret on the PC is not touched.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep account</AlertDialogCancel>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirming(false);
                void run(
                  "remove_connection",
                  { connection_id: connection.id },
                  { key: `remove:${connection.id}`, label: `Remove ${connection.id}` },
                );
              }}
            >
              Remove account
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
