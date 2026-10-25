"use client";

import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { LoginSession } from "@/lib/farm/types";

import { SignInCard } from "./sign-in-card";

export interface SignInDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connectionId?: string;
  sessionId?: string;
  providerId?: string;
  title?: string;
  onDone?: (session: LoginSession) => void;
}

export function SignInDrawer({ open, onOpenChange, connectionId, sessionId, providerId, title, onDone }: SignInDrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent data-testid="sign-in-drawer" side="right" className="w-full overflow-y-auto p-6 sm:max-w-md">
        <SheetHeader className="mb-4">
          <SheetTitle>{title ?? "Sign in account"}</SheetTitle>
          <SheetDescription className="text-xs">
            Complete authentication without leaving the Console dashboard.
          </SheetDescription>
        </SheetHeader>
        {open && Boolean(connectionId ?? sessionId) && (
          <SignInCard
            connectionId={connectionId}
            sessionId={sessionId}
            providerId={providerId}
            onDone={onDone}
            onCancelled={() => onOpenChange(false)}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}
