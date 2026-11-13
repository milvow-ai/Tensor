"use client";

import { useState } from "react";

import { ArrowLeft, Plus } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { AI_CLI_INFO, AI_CLIS, type AiCli } from "@/lib/farm/command-meta";
import { runQueuedCommand } from "@/lib/farm/queued-command";

import { SignInCard } from "./sign-in-card";

export interface AddAccountsDialogProps {
  defaultProviderId?: string;
  trigger?: React.ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onAdded?: (connectionIds: string[], sessionIds: string[]) => void;
}

export function AddAccountsDialog({
  defaultProviderId = "claude",
  trigger,
  onAdded,
  open: controlledOpen,
  onOpenChange,
}: AddAccountsDialogProps) {
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const setOpen = (next: boolean) => {
    setLocalOpen(next);
    onOpenChange?.(next);
  };
  const [step, setStep] = useState<"form" | "stack">("form");
  const [loading, setLoading] = useState(false);

  // Form State
  const [cli, setCli] = useState<AiCli>(
    () =>
      AI_CLIS.find((item) => item !== "agy" && item === defaultProviderId) ??
      AI_CLIS.find((item) => AI_CLI_INFO[item].providerId === defaultProviderId) ??
      "claude",
  );
  const providerId = AI_CLI_INFO[cli].providerId;
  const [namesText, setNamesText] = useState("");
  const [emailsText, setEmailsText] = useState<string>("");

  // Stack State (created sessions)
  const [createdSessionIds, setCreatedSessionIds] = useState<string[]>([]);
  const [sessionsByConnection, setSessionsByConnection] = useState<Record<string, string>>({});
  const [createdConnectionIds, setCreatedConnectionIds] = useState<string[]>([]);

  const resetForm = () => {
    setStep("form");
    setEmailsText("");
    setNamesText("");
    setCreatedSessionIds([]);
    setCreatedConnectionIds([]);
    setLoading(false);
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next && !loading && createdConnectionIds.length === 0) resetForm();
  };

  const parseEmails = (): string[] => {
    return emailsText
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const emails = parseEmails();
      if (!emails.length || emails.length > 10) throw new Error("Enter one to ten expected email addresses.");
      const key = `add-accounts:${providerId}`;
      const cmd = await runQueuedCommand(key, "add_accounts", {
        provider_id: providerId,
        count: emails.length,
        cli,
        emails,
        names: namesText.trim()
          ? namesText
              .trimEnd()
              .split("\n")
              .map((name) => name.trim())
          : [],
      });

      const result = cmd.result as
        | {
            connection_ids?: string[];
            session_ids?: string[];
            accounts?: { connection_id: string; session_id: string | null }[];
          }
        | undefined;

      const connIds = result?.connection_ids ?? [];
      const sessIds = result?.session_ids ?? [];

      setCreatedConnectionIds(connIds);
      setCreatedSessionIds(sessIds);
      setSessionsByConnection(
        Object.fromEntries(
          (result?.accounts ?? connIds.map((connection_id, index) => ({ connection_id, session_id: sessIds[index] })))
            .filter((a) => a.session_id)
            .map((a) => [a.connection_id, a.session_id as string]),
        ),
      );
      if (connIds.length) setStep("stack");
      if (cmd.status !== "done") {
        throw new Error(
          typeof cmd.result?.error === "string"
            ? cmd.result.error
            : "Some accounts need a retry. Existing account setups are preserved.",
        );
      }
      toast.success(`Created ${connIds.length} account setup${connIds.length > 1 ? "s" : ""}`);
      onAdded?.(connIds, sessIds);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to add accounts");
    } finally {
      setLoading(false);
    }
  };

  const handleRemoveSlot = (connectionId: string) => {
    const connIdx = createdConnectionIds.indexOf(connectionId);
    setCreatedConnectionIds((prev) => prev.filter((id) => id !== connectionId));
    if (connIdx !== -1) {
      setCreatedSessionIds((prev) => prev.filter((_, i) => i !== connIdx));
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        {trigger ? (
          trigger
        ) : (
          <Button size="sm" data-testid="add-accounts-button" className="gap-1.5">
            <Plus className="size-4" />+ Add accounts
          </Button>
        )}
      </DialogTrigger>

      <DialogContent data-testid="add-accounts-dialog" className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <div className="flex items-center gap-2">
            {step === "stack" && (
              <Button variant="ghost" size="icon" className="size-7" onClick={() => setStep("form")}>
                <ArrowLeft className="size-4" />
              </Button>
            )}
            <DialogTitle>{step === "form" ? "Add AI accounts" : "Sign in your new accounts"}</DialogTitle>
          </div>
          <DialogDescription className="text-xs">
            {step === "form"
              ? "Add up to 10 accounts to this pool and sign them all in through dashboard cards."
              : `Complete sign-in for the ${createdSessionIds.length} newly added accounts.`}
          </DialogDescription>
        </DialogHeader>

        {step === "form" ? (
          <form onSubmit={handleSubmit} className="space-y-4 pt-1">
            {/* Provider */}
            <div className="space-y-1.5">
              <Label htmlFor="provider-select" className="font-medium text-xs">
                Provider
              </Label>
              <NativeSelect
                id="provider-select"
                data-testid="provider-select"
                value={cli}
                onChange={(e) => setCli(e.target.value as AiCli)}
                className="w-full"
              >
                {AI_CLIS.filter((item) => item !== "agy").map((cli) => (
                  <NativeSelectOption key={cli} value={cli}>
                    {AI_CLI_INFO[cli].label} ({AI_CLI_INFO[cli].providerId})
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="names-input">Account names (optional, one per line)</Label>
              <Textarea id="names-input" value={namesText} onChange={(e) => setNamesText(e.target.value)} />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="emails-input" className="font-medium text-xs">
                Expected emails per account (required)
              </Label>
              <Textarea
                id="emails-input"
                data-testid="emails-input"
                required
                placeholder="One email per line or comma-separated"
                rows={3}
                value={emailsText}
                onChange={(e) => setEmailsText(e.target.value)}
                className="font-mono text-xs"
              />
              <p className="text-[11px] text-muted-foreground">
                Enter one expected email per account. The email must match the provider sign-in.
              </p>
            </div>

            <DialogFooter className="pt-2">
              <Button type="button" variant="ghost" onClick={() => setOpen(false)} disabled={loading}>
                Cancel
              </Button>
              <Button type="submit" data-testid="add-accounts-submit" disabled={loading}>
                {loading ? <Spinner className="mr-2 size-4" /> : null}
                {loading ? "Waiting for Farm (safe to close)" : "Create accounts & sign in"}
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <div className="space-y-4 pt-1">
            <div className="text-muted-foreground text-xs">
              Sign in each account below. You can complete them in parallel.
            </div>

            <div className="space-y-4">
              {createdConnectionIds.map((connId) => (
                <SignInCard
                  key={connId}
                  sessionId={sessionsByConnection[connId]}
                  connectionId={connId}
                  providerId={providerId}
                  onRemoveSlot={handleRemoveSlot}
                />
              ))}
            </div>

            <DialogFooter className="border-border border-t pt-2">
              <Button type="button" onClick={() => setOpen(false)}>
                Done
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
