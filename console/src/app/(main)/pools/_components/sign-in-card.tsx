"use client";

import { useState } from "react";

import Link from "next/link";

import { CheckCircle2, Copy, ExternalLink, Loader2, Lock, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Countdown } from "@/components/farm/time";
import { useLoginSession } from "@/components/farm/use-login-session";
import { StatusChip } from "@/components/kit/StatusChip";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { submitCommand } from "@/lib/farm/actions";
import type { LoginSession } from "@/lib/farm/types";

export interface SignInCardProps {
  sessionId?: string;
  connectionId?: string;
  initialSession?: LoginSession | null;
  onDone?: (session: LoginSession) => void;
  onCancelled?: () => void;
  onRemoveSlot?: (connectionId: string) => void;
  className?: string;
}

export function SignInCard({
  sessionId,
  connectionId,
  initialSession,
  onDone,
  onCancelled,
  onRemoveSlot,
  className = "",
}: SignInCardProps) {
  const { session, viewModel, loading, error, canSignIn, startLogin, submitInput, cancelLogin } = useLoginSession({
    sessionId,
    connectionId,
    onDone,
    onCancelled,
  });

  const [inputCode, setInputCode] = useState("");
  const [submittingInput, setSubmittingInput] = useState(false);
  const [removingSlot, setRemovingSlot] = useState(false);

  // Copy helper
  const copy = async (text?: string | null, label = "Code") => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied to clipboard`);
    } catch {
      toast.error(`Failed to copy ${label.toLowerCase()}`);
    }
  };

  const currentSession = session ?? initialSession;

  if (loading && !viewModel && !currentSession) {
    return (
      <Card data-testid="sign-in-card" className={`rounded-xl border border-border bg-card p-5 ${className}`}>
        <div className="flex items-center gap-3">
          <Spinner className="size-4" />
          <span className="text-muted-foreground text-sm">Loading sign-in session…</span>
        </div>
      </Card>
    );
  }

  if (!canSignIn) {
    return (
      <Card
        data-testid="sign-in-card"
        className={`rounded-xl border border-amber-500/30 bg-amber-500/5 p-5 ${className}`}
      >
        <div className="flex items-start gap-3">
          <Lock className="mt-0.5 size-5 shrink-0 text-amber-600" />
          <div className="space-y-1">
            <h4 className="font-medium text-foreground text-sm">Sign-in unavailable</h4>
            <p className="text-muted-foreground text-xs">
              Start the Farm to sign accounts in (encryption public key is not yet published).
            </p>
          </div>
        </div>
      </Card>
    );
  }

  if (error && !viewModel) {
    return (
      <Card
        data-testid="sign-in-card"
        className={`rounded-xl border border-destructive/30 bg-destructive/5 p-5 ${className}`}
      >
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 size-5 shrink-0 text-destructive" />
          <div className="space-y-2">
            <h4 className="font-medium text-foreground text-sm">Failed to load sign-in session</h4>
            <p className="text-muted-foreground text-xs">{error}</p>
            {connectionId && (
              <Button size="sm" variant="outline" onClick={() => void startLogin()}>
                Retry sign-in
              </Button>
            )}
          </div>
        </div>
      </Card>
    );
  }

  if (!viewModel) {
    return (
      <Card data-testid="sign-in-card" className={`rounded-xl border border-border bg-card p-5 ${className}`}>
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <h4 className="font-medium text-foreground text-sm">Sign in account {connectionId ?? sessionId}</h4>
            <p className="text-muted-foreground text-xs">No active sign-in session in progress.</p>
          </div>
          {connectionId && (
            <Button size="sm" onClick={() => void startLogin()}>
              Sign in
            </Button>
          )}
        </div>
      </Card>
    );
  }

  // Handle submit code
  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputCode.trim() || submittingInput) return;
    setSubmittingInput(true);
    const code = inputCode;
    // Clear state input immediately for security
    setInputCode("");
    const ok = await submitInput(code);
    setSubmittingInput(false);
    if (!ok) {
      // If failed, restore input so user can edit or retry
      setInputCode(code);
    }
  };

  // Handle remove slot
  const handleRemoveSlot = async () => {
    if (!viewModel.connectionId) return;
    setRemovingSlot(true);
    try {
      if (onRemoveSlot) {
        onRemoveSlot(viewModel.connectionId);
      } else {
        await submitCommand("remove_connection", { connection_id: viewModel.connectionId });
        toast.success(`Slot ${viewModel.connectionId} removed`);
      }
    } catch {
      toast.error("Failed to remove slot");
    } finally {
      setRemovingSlot(false);
    }
  };

  const effectiveConnectionId = viewModel.connectionId || connectionId;

  return (
    <Card
      data-testid="sign-in-card"
      data-state={viewModel.state}
      className={`space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm ${className}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-foreground text-sm tracking-tight">{viewModel.title}</h3>
            {effectiveConnectionId && (
              <span className="font-mono text-muted-foreground text-xs">({effectiveConnectionId})</span>
            )}
          </div>
          {viewModel.description && <p className="mt-1 text-muted-foreground text-xs">{viewModel.description}</p>}
        </div>
        <StatusChip status={viewModel.statusChipStatus} label={viewModel.statusChipLabel} size="sm" />
      </div>

      {/* State-specific Body */}
      {(viewModel.state === "queued" || viewModel.state === "starting") && (
        <div className="flex items-center gap-3 py-3 text-muted-foreground">
          <Spinner className="size-4 animate-spin text-primary" />
          <span className="text-xs">Getting the sign-in ready…</span>
        </div>
      )}

      {viewModel.state === "waiting_for_user" && viewModel.userCode && (
        <div className="space-y-3.5 pt-1">
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-border bg-muted/40 p-4">
            <span className="font-medium text-[11px] text-muted-foreground uppercase tracking-wider">
              Your device code
            </span>
            <div
              data-testid="device-code"
              className="select-all font-bold font-mono text-2xl text-foreground tracking-widest"
            >
              {viewModel.userCode}
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-1 gap-1.5 text-xs"
              onClick={() => void copy(viewModel.userCode, "Device code")}
            >
              <Copy className="size-3.5" />
              Copy code
            </Button>
          </div>

          <div className="space-y-2 text-xs">
            {viewModel.verificationUri && (
              <Button asChild size="sm" className="w-full gap-2">
                <a href={viewModel.verificationUri} target="_blank" rel="noopener noreferrer" data-testid="auth-link">
                  <ExternalLink className="size-3.5" />
                  Open sign-in page
                </a>
              </Button>
            )}
            <p className="text-[11px] text-muted-foreground leading-relaxed">
              Use a private window if this browser is signed in to another account.
            </p>
            {viewModel.expiresAt && (
              <p className="text-[11px] text-muted-foreground">
                Code expires in: <Countdown until={viewModel.expiresAt} className="font-medium text-foreground" />
              </p>
            )}
          </div>
        </div>
      )}

      {viewModel.state === "waiting_for_user" && viewModel.needsInput && (
        <form onSubmit={handleConnect} className="space-y-3.5 pt-1">
          {viewModel.authUrl && (
            <div>
              <Button asChild variant="outline" size="sm" className="gap-2 text-xs">
                <a href={viewModel.authUrl} target="_blank" rel="noopener noreferrer" data-testid="auth-link">
                  <ExternalLink className="size-3.5" />
                  Sign in at {viewModel.providerId || "provider"}
                </a>
              </Button>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor={`code-input-${viewModel.sessionId}`} className="font-medium text-xs">
              Paste the code the page shows you
            </Label>
            <div className="flex gap-2">
              <Input
                id={`code-input-${viewModel.sessionId}`}
                data-testid="input-code"
                autoComplete="off"
                spellCheck={false}
                placeholder="Paste authorization code here"
                value={inputCode}
                onChange={(e) => setInputCode(e.target.value)}
                className="font-mono text-xs"
                disabled={submittingInput}
              />
              <Button
                type="submit"
                size="sm"
                data-testid="connect-button"
                disabled={!inputCode.trim() || submittingInput}
                className="shrink-0"
              >
                {submittingInput ? <Spinner className="mr-1.5 size-3.5" /> : null}
                Connect
              </Button>
            </div>
            {viewModel.inputHint && <p className="text-[11px] text-muted-foreground">{viewModel.inputHint}</p>}
          </div>

          {viewModel.expiresAt && (
            <p className="text-[11px] text-muted-foreground">
              Expires in: <Countdown until={viewModel.expiresAt} className="font-medium text-foreground" />
            </p>
          )}
        </form>
      )}

      {viewModel.state === "verifying" && (
        <div className="space-y-3 pt-1">
          <div className="flex items-center gap-2 font-medium text-primary text-xs">
            <Loader2 className="size-4 animate-spin" />
            <span>Checking…</span>
          </div>
          {viewModel.checklist && (
            <ul className="space-y-1.5 border-border border-t pt-2 text-muted-foreground text-xs">
              {viewModel.checklist.map((item) => (
                <li key={item} className="flex items-center gap-2">
                  <CheckCircle2 className="size-3.5 text-emerald-600 dark:text-emerald-400" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {viewModel.state === "done" && (
        <div className="space-y-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3.5 text-xs">
          <div className="flex items-center justify-between">
            <span className="font-medium text-emerald-700 dark:text-emerald-400">Account ready to serve requests</span>
            {viewModel.identity?.plan && (
              <span className="rounded border bg-background px-2 py-0.5 font-medium text-foreground">
                {viewModel.identity.plan}
              </span>
            )}
          </div>

          {viewModel.identity?.email_masked && (
            <div className="text-muted-foreground">
              Signed in as: <span className="font-medium text-foreground">{viewModel.identity.email_masked}</span>
            </div>
          )}

          {viewModel.evidence?.models && viewModel.evidence.models.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              <span className="text-[11px] text-muted-foreground">Models:</span>
              {viewModel.evidence.models.map((m) => (
                <span
                  key={m}
                  className="rounded border bg-background px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                >
                  {m}
                </span>
              ))}
            </div>
          )}

          {viewModel.evidence?.test_run_id && (
            <div className="pt-1">
              <Button asChild variant="link" size="sm" className="h-auto p-0 text-xs">
                <Link href={`/runs/${viewModel.evidence.test_run_id}`}>View verification run</Link>
              </Button>
            </div>
          )}
        </div>
      )}

      {viewModel.state === "duplicate" && (
        <div className="space-y-3 rounded-lg border border-destructive/20 bg-destructive/5 p-3.5 text-xs">
          <p className="font-medium text-destructive">Duplicate account</p>
          {viewModel.errorExcerpt && <p className="text-muted-foreground">{viewModel.errorExcerpt}</p>}
          <div className="flex flex-wrap gap-2 pt-1">
            <Button type="button" size="sm" variant="outline" onClick={() => void startLogin()}>
              Try again in a private window
            </Button>
            <Button
              type="button"
              size="sm"
              variant="destructive"
              disabled={removingSlot}
              onClick={() => void handleRemoveSlot()}
            >
              {removingSlot ? <Spinner className="mr-1.5 size-3.5" /> : null}
              Remove this slot
            </Button>
          </div>
        </div>
      )}

      {viewModel.state === "needs_verification" && (
        <div className="space-y-3 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3.5 text-xs">
          <p className="font-medium text-amber-700 dark:text-amber-400">The provider asks you to verify this account</p>
          {viewModel.errorExcerpt && <p className="text-muted-foreground">{viewModel.errorExcerpt}</p>}
          <div className="flex flex-wrap gap-2 pt-1">
            {viewModel.authUrl && (
              <Button asChild size="sm" variant="default">
                <a href={viewModel.authUrl} target="_blank" rel="noopener noreferrer">
                  <ExternalLink className="mr-1.5 size-3.5" />
                  Verify
                </a>
              </Button>
            )}
            <Button type="button" size="sm" variant="outline" onClick={() => void startLogin()}>
              Check again
            </Button>
          </div>
        </div>
      )}

      {(viewModel.state === "failed" || viewModel.state === "expired" || viewModel.state === "cancelled") && (
        <div className="space-y-3 rounded-lg border border-destructive/20 bg-destructive/5 p-3.5 text-xs">
          {viewModel.errorExcerpt && <p className="text-muted-foreground">{viewModel.errorExcerpt}</p>}
          <div className="pt-1">
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid="start-again-button"
              onClick={() => void startLogin()}
            >
              Start again
            </Button>
          </div>
        </div>
      )}

      {/* Footer Actions (Cancel on non-terminal states) */}
      {viewModel.canCancel && (
        <div className="flex justify-end border-border border-t pt-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="cancel-login-button"
            className="text-muted-foreground text-xs hover:text-foreground"
            onClick={() => void cancelLogin()}
          >
            Cancel
          </Button>
        </div>
      )}
    </Card>
  );
}
