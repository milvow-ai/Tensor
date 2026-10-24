"use client";

import { useState } from "react";

import { ArrowLeft, Minus, Plus } from "lucide-react";
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
import { readCommand, submitCommand } from "@/lib/farm/actions";
import { AI_CLI_INFO, AI_CLIS, type AiCli } from "@/lib/farm/command-meta";

import { SignInCard } from "./sign-in-card";

export interface AddAccountsDialogProps {
  defaultProviderId?: string;
  trigger?: React.ReactNode;
  onAdded?: (connectionIds: string[], sessionIds: string[]) => void;
}

const PLANS = ["Free", "Pro", "Max", "Team"] as const;

export function AddAccountsDialog({ defaultProviderId = "claude", trigger, onAdded }: AddAccountsDialogProps) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<"form" | "stack">("form");
  const [loading, setLoading] = useState(false);

  // Form State
  const [providerId, setProviderId] = useState<string>(defaultProviderId);
  const [count, setCount] = useState<number>(1);
  const [plan, setPlan] = useState<string>("Pro");
  const [emailsText, setEmailsText] = useState<string>("");
  const [maxParallel, setMaxParallel] = useState<number>(1);

  // Stack State (created sessions)
  const [createdSessionIds, setCreatedSessionIds] = useState<string[]>([]);
  const [createdConnectionIds, setCreatedConnectionIds] = useState<string[]>([]);

  const resetForm = () => {
    setStep("form");
    setCount(1);
    setEmailsText("");
    setMaxParallel(1);
    setCreatedSessionIds([]);
    setCreatedConnectionIds([]);
    setLoading(false);
  };

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) {
      resetForm();
    }
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
      const cli = AI_CLIS.includes(providerId as AiCli) ? (providerId as AiCli) : undefined;

      const res = await submitCommand("add_accounts", {
        provider_id: providerId,
        count,
        cli,
        plan,
        emails: emails.length > 0 ? emails : undefined,
        max_parallel: maxParallel,
      });

      if (!res.ok) {
        toast.error(res.error || "Failed to add accounts");
        setLoading(false);
        return;
      }

      const cmdId = res.data.id;
      let cmd = res.data;
      const started = Date.now();
      while (cmd.status === "queued" || cmd.status === "running") {
        if (Date.now() - started > 15_000) break;
        await new Promise((r) => setTimeout(r, 200));
        const polled = await readCommand(cmdId);
        if (polled.ok && polled.data) {
          cmd = polled.data;
        }
      }

      if (cmd.status !== "done") {
        toast.error(cmd.status === "rejected" ? "Failed to add accounts: rejected" : "Failed to add accounts");
        setLoading(false);
        return;
      }

      const result = cmd.result as
        | {
            connection_ids?: string[];
            session_ids?: string[];
          }
        | undefined;

      const connIds = result?.connection_ids ?? [];
      const sessIds = result?.session_ids ?? [];

      setCreatedConnectionIds(connIds);
      setCreatedSessionIds(sessIds);
      setStep("stack");
      toast.success(`Created ${count} account slot${count > 1 ? "s" : ""}`);
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
              : `Complete sign-in for the ${createdSessionIds.length} newly added slots.`}
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
                value={providerId}
                onChange={(e) => setProviderId(e.target.value)}
                className="w-full"
              >
                {AI_CLIS.map((cli) => (
                  <NativeSelectOption key={cli} value={AI_CLI_INFO[cli].providerId}>
                    {AI_CLI_INFO[cli].label} ({AI_CLI_INFO[cli].providerId})
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>

            {/* Count Stepper */}
            <div className="space-y-1.5">
              <Label className="font-medium text-xs">Number of accounts (1–10)</Label>
              <div className="flex items-center gap-3">
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="size-8"
                  disabled={count <= 1}
                  onClick={() => setCount((c) => Math.max(1, c - 1))}
                >
                  <Minus className="size-3.5" />
                </Button>
                <span
                  data-testid="count-stepper"
                  className="w-8 text-center font-mono font-semibold text-base tabular-nums"
                >
                  {count}
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="size-8"
                  disabled={count >= 10}
                  onClick={() => setCount((c) => Math.min(10, c + 1))}
                >
                  <Plus className="size-3.5" />
                </Button>
                <span className="ml-2 text-muted-foreground text-xs">
                  {count === 1 ? "1 new slot" : `${count} new slots`}
                </span>
              </div>
            </div>

            {/* Plan */}
            <div className="space-y-1.5">
              <Label htmlFor="plan-select" className="font-medium text-xs">
                Plan
              </Label>
              <NativeSelect
                id="plan-select"
                data-testid="plan-select"
                value={plan}
                onChange={(e) => setPlan(e.target.value)}
                className="w-full"
              >
                {PLANS.map((p) => (
                  <NativeSelectOption key={p} value={p}>
                    {p}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>

            {/* Concurrency / Max Parallel */}
            <div className="space-y-1.5">
              <Label htmlFor="max-parallel-select" className="font-medium text-xs">
                Jobs at a time per account (1–4)
              </Label>
              <NativeSelect
                id="max-parallel-select"
                value={String(maxParallel)}
                onChange={(e) => setMaxParallel(Number(e.target.value))}
                className="w-full"
              >
                <NativeSelectOption value="1">1 concurrent job</NativeSelectOption>
                <NativeSelectOption value="2">2 concurrent jobs</NativeSelectOption>
                <NativeSelectOption value="3">3 concurrent jobs</NativeSelectOption>
                <NativeSelectOption value="4">4 concurrent jobs</NativeSelectOption>
              </NativeSelect>
            </div>

            {/* Optional Expected Emails */}
            <div className="space-y-1.5">
              <Label htmlFor="emails-input" className="font-medium text-xs">
                Expected emails per slot (optional)
              </Label>
              <Textarea
                id="emails-input"
                data-testid="emails-input"
                placeholder="One email per line or comma-separated"
                rows={3}
                value={emailsText}
                onChange={(e) => setEmailsText(e.target.value)}
                className="font-mono text-xs"
              />
              <p className="text-[11px] text-muted-foreground">Helps verify the correct account signed in.</p>
            </div>

            <DialogFooter className="pt-2">
              <Button type="button" variant="ghost" onClick={() => setOpen(false)} disabled={loading}>
                Cancel
              </Button>
              <Button type="submit" data-testid="add-accounts-submit" disabled={loading}>
                {loading ? <Spinner className="mr-2 size-4" /> : null}
                Create slots & sign in
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <div className="space-y-4 pt-1">
            <div className="text-muted-foreground text-xs">
              Sign in each account below. You can complete them in parallel.
            </div>

            <div className="space-y-4">
              {createdSessionIds.map((sessionId, index) => (
                <SignInCard
                  key={sessionId}
                  sessionId={sessionId}
                  connectionId={createdConnectionIds[index]}
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
