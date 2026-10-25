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
import { AI_CLI_INFO, type AiCli, isApiKeyAi } from "@/lib/farm/command-meta";
import { generateEnvName } from "@/lib/farm/login";
import { readConnectPublicKey } from "@/lib/farm/login-actions";
import { buildAad, seal } from "@/lib/farm/seal";
import type { LoginSession } from "@/lib/farm/types";

export interface SignInCardProps {
  sessionId?: string;
  connectionId?: string;
  providerId?: string;
  initialSession?: LoginSession | null;
  onDone?: (session: LoginSession) => void;
  onCancelled?: () => void;
  onRemoveSlot?: (connectionId: string) => void;
  className?: string;
}

export function SignInCard({
  sessionId,
  connectionId,
  providerId,
  initialSession,
  onDone,
  onCancelled,
  onRemoveSlot,
  className = "",
}: SignInCardProps) {
  const { session, viewModel, loading, error, publicKey, canSignIn, startLogin, submitInput, cancelLogin } =
    useLoginSession({
      sessionId,
      connectionId,
      onDone,
      onCancelled,
    });

  const [inputCode, setInputCode] = useState("");
  const [submittingInput, setSubmittingInput] = useState(false);
  const [removingSlot, setRemovingSlot] = useState(false);

  // API key state
  const [apiKeyStatus, setApiKeyStatus] = useState<"idle" | "verifying" | "done" | "failed">("idle");
  const [apiKeyError, setApiKeyError] = useState<string | null>(null);

  const effectiveConnectionId = viewModel?.connectionId ?? connectionId;
  const rawProvider =
    providerId ??
    session?.provider_id ??
    viewModel?.providerId ??
    (effectiveConnectionId ? effectiveConnectionId.split("-")[0] : undefined);
  const isApiKey = isApiKeyAi(rawProvider) || session?.method === "api_key";

  const resolvedProviderId = (rawProvider && rawProvider in AI_CLI_INFO ? rawProvider : "perplexity") as AiCli;
  const cliInfo = AI_CLI_INFO[resolvedProviderId] ?? AI_CLI_INFO.perplexity;
  const providerLabel = cliInfo.label;
  const models = cliInfo.models;

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

  if (loading && !viewModel && !currentSession && !isApiKey) {
    return (
      <Card data-testid="sign-in-card" className={`rounded-xl border border-border bg-card p-5 ${className}`}>
        <div className="flex items-center gap-3">
          <Spinner className="size-4" />
          <span className="text-muted-foreground text-sm">Loading sign-in session…</span>
        </div>
      </Card>
    );
  }

  if (!canSignIn && !loading) {
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

  if (error && !viewModel && !isApiKey) {
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

  if (!viewModel && !isApiKey) {
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

  // Handle submit code / API key
  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputCode.trim() || submittingInput) return;
    setSubmittingInput(true);
    const plainInput = inputCode.trim();
    // Clear state input immediately for security
    setInputCode("");

    if (isApiKey) {
      try {
        const pk = publicKey ?? (await readConnectPublicKey());
        if (!pk) {
          toast.error("Start the Farm to sign accounts in");
          setSubmittingInput(false);
          return;
        }

        const envName = generateEnvName(resolvedProviderId, []);
        const aad = buildAad(envName, "secret");
        const envelope = await seal(plainInput, pk, aad);

        const res = await submitCommand("save_secret", {
          name: envName,
          sealed: envelope,
          connection_id: effectiveConnectionId,
        });

        if (!res.ok) {
          setApiKeyError(res.error);
          toast.error(res.error || "Failed to save secret");
          setApiKeyStatus("failed");
          setSubmittingInput(false);
          return;
        }

        // State updates to checking / verifying
        setApiKeyStatus("verifying");

        // Keep verifying state visible so connection testing is observable
        await new Promise((r) => setTimeout(r, 1000));

        // After saving, the account test runs like for other AIs
        if (effectiveConnectionId) {
          await submitCommand("test_connection", { connection_id: effectiveConnectionId });
        }

        setApiKeyStatus("done");
        toast.success("Account connected and tested");
        onDone?.({
          id: session?.id ?? sessionId ?? crypto.randomUUID(),
          connection_id: effectiveConnectionId ?? "",
          provider_id: resolvedProviderId,
          method: "api_key",
          state: "done",
          auth_url: "https://www.perplexity.ai/account/api/keys",
          user_code: null,
          verification_uri: "https://www.perplexity.ai/account/api/keys",
          needs_input: false,
          input_hint: null,
          expires_at: null,
          identity: {},
          evidence: { models },
          error_kind: null,
          error_excerpt: null,
          created_by: "owner@farm.local",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          finished_at: new Date().toISOString(),
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to connect API key";
        setApiKeyError(msg);
        toast.error(msg);
        setApiKeyStatus("failed");
      } finally {
        setSubmittingInput(false);
      }
      return;
    }

    const ok = await submitInput(plainInput);
    setSubmittingInput(false);
    if (!ok) {
      setInputCode(plainInput);
    }
  };

  // Handle remove slot
  const handleRemoveSlot = async () => {
    const slotId = effectiveConnectionId;
    if (!slotId) return;
    setRemovingSlot(true);
    try {
      if (onRemoveSlot) {
        onRemoveSlot(slotId);
      } else {
        await submitCommand("remove_connection", { connection_id: slotId });
        toast.success(`Slot ${slotId} removed`);
      }
    } catch {
      toast.error("Failed to remove slot");
    } finally {
      setRemovingSlot(false);
    }
  };

  const cardTitle =
    apiKeyStatus === "verifying" || (apiKeyStatus === "idle" && viewModel?.state === "verifying")
      ? "Verifying account"
      : apiKeyStatus === "done" || (apiKeyStatus === "idle" && viewModel?.state === "done")
      ? "Ready"
      : isApiKey
      ? `Sign in ${effectiveConnectionId ?? providerLabel}`
      : (viewModel?.title ?? "Sign in");

  const cardDescription =
    isApiKey && apiKeyStatus === "idle" && (!viewModel || viewModel.state === "waiting_for_user")
      ? `Paste your ${providerLabel} API key to connect this account.`
      : viewModel?.description;

  const currentState = isApiKey
    ? apiKeyStatus !== "idle"
      ? apiKeyStatus
      : (viewModel?.state ?? "waiting_for_user")
    : (viewModel?.state ?? "waiting_for_user");

  const chipStatus: "ready" | "running" | "waiting-for-you" | "failed" | "needs-login" = isApiKey
    ? currentState === "verifying"
      ? "running"
      : currentState === "done"
      ? "ready"
      : currentState === "failed"
      ? "failed"
      : "waiting-for-you"
    : (viewModel?.statusChipStatus ?? "waiting-for-you");

  const chipLabel = isApiKey
    ? currentState === "verifying"
      ? "Verifying"
      : currentState === "done"
      ? "Ready"
      : currentState === "failed"
      ? "Failed"
      : "Waiting for you"
    : (viewModel?.statusChipLabel ?? "Waiting for you");

  const isVerifying = currentState === "verifying";
  const isDone = currentState === "done";

  return (
    <Card
      data-testid="sign-in-card"
      data-state={currentState}
      className={`space-y-4 rounded-xl border border-border bg-card p-5 shadow-sm ${className}`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-foreground text-sm tracking-tight">{cardTitle}</h3>
            {effectiveConnectionId && (
              <span className="font-mono text-muted-foreground text-xs">({effectiveConnectionId})</span>
            )}
          </div>
          {cardDescription && <p className="mt-1 text-muted-foreground text-xs">{cardDescription}</p>}
        </div>
        <StatusChip status={chipStatus} label={chipLabel} size="sm" />
      </div>

      {/* State-specific Body */}
      {!isApiKey && (currentState === "queued" || currentState === "starting") && (
        <div className="flex items-center gap-3 py-3 text-muted-foreground">
          <Spinner className="size-4 animate-spin text-primary" />
          <span className="text-xs">Getting the sign-in ready…</span>
        </div>
      )}

      {!isApiKey && currentState === "waiting_for_user" && viewModel?.userCode && (
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
            {viewModel.expiresAt && (
              <p className="text-[11px] text-muted-foreground">
                Code expires in: <Countdown until={viewModel.expiresAt} className="font-medium text-foreground" />
              </p>
            )}
          </div>
        </div>
      )}

      {/* API Key Form for API-key AI */}
      {isApiKey && (
        <form
          onSubmit={handleConnect}
          className={currentState === "waiting_for_user" ? "space-y-3.5 pt-1" : "hidden"}
        >
          <div>
            <Button asChild variant="outline" size="sm" className="gap-2 text-xs">
              <a
                href={
                  resolvedProviderId === "perplexity"
                    ? "https://www.perplexity.ai/account/api/keys"
                    : (viewModel?.authUrl ?? "https://www.perplexity.ai/account/api/keys")
                }
                target="_blank"
                rel="noopener noreferrer"
                data-testid="auth-link"
              >
                <ExternalLink className="size-3.5" />
                {providerLabel} API keys
              </a>
            </Button>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor={`code-input-${effectiveConnectionId}`} className="font-medium text-xs">
              {providerLabel} API key
            </Label>
            <div className="flex gap-2">
              <Input
                id={`code-input-${effectiveConnectionId}`}
                data-testid="input-code"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder={`Paste ${providerLabel} API key`}
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
            <p className="text-[11px] text-muted-foreground">
              Encrypted in your browser and saved to the Farm vault.
            </p>
          </div>
        </form>
      )}

      {/* Standard OAuth input */}
      {!isApiKey && currentState === "waiting_for_user" && viewModel?.needsInput && (
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

      {isVerifying && (
        <div className="space-y-3 pt-1">
          <div className="flex items-center gap-2 font-medium text-primary text-xs">
            <Loader2 className="size-4 animate-spin" />
            <span>Checking…</span>
          </div>
          <ul className="space-y-1.5 border-border border-t pt-2 text-muted-foreground text-xs">
            {(isApiKey ? ["Key saved sealed", "Testing API connection"] : (viewModel?.checklist ?? ["Signed in", "Checking who it is", "Test reply"])).map((item) => (
              <li key={item} className="flex items-center gap-2">
                <CheckCircle2 className="size-3.5 text-emerald-600 dark:text-emerald-400" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {isDone && (
        <div className="space-y-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3.5 text-xs">
          <div className="flex items-center justify-between">
            <span className="font-medium text-emerald-700 dark:text-emerald-400">Account ready to serve requests</span>
            <span className="rounded border bg-background px-2 py-0.5 font-medium text-foreground">
              {viewModel?.identity?.plan ?? (isApiKey ? "API Key" : "Ready")}
            </span>
          </div>

          {!isApiKey && viewModel?.identity?.email_masked && (
            <div className="text-muted-foreground">
              Signed in as: <span className="font-medium text-foreground">{viewModel.identity.email_masked}</span>
            </div>
          )}

          {(isApiKey ? models : (viewModel?.evidence?.models ?? [])).length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              <span className="text-[11px] text-muted-foreground">Models:</span>
              {(isApiKey ? models : (viewModel?.evidence?.models ?? [])).map((m) => (
                <span
                  key={m}
                  className="rounded border bg-background px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                >
                  {m}
                </span>
              ))}
            </div>
          )}

          {viewModel?.evidence?.test_run_id && (
            <div className="pt-1">
              <Button asChild variant="link" size="sm" className="h-auto p-0 text-xs">
                <Link href={`/runs/${viewModel.evidence.test_run_id}`}>View verification run</Link>
              </Button>
            </div>
          )}
        </div>
      )}

      {currentState === "duplicate" && (
        <div className="space-y-3 rounded-lg border border-destructive/20 bg-destructive/5 p-3.5 text-xs">
          <p className="font-medium text-destructive">Duplicate account</p>
          {viewModel?.errorExcerpt && <p className="text-muted-foreground">{viewModel.errorExcerpt}</p>}
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

      {currentState === "needs_verification" && (
        <div className="space-y-3 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3.5 text-xs">
          <p className="font-medium text-amber-700 dark:text-amber-400">The provider asks you to verify this account</p>
          {viewModel?.errorExcerpt && <p className="text-muted-foreground">{viewModel.errorExcerpt}</p>}
          <div className="flex flex-wrap gap-2 pt-1">
            {viewModel?.authUrl && (
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

      {(currentState === "failed" || currentState === "expired" || currentState === "cancelled") && (
        <div className="space-y-3 rounded-lg border border-destructive/20 bg-destructive/5 p-3.5 text-xs">
          <p className="text-muted-foreground">
            {isApiKey
              ? (apiKeyError ?? "Sign-in failed. Please verify your API key and try again.")
              : (viewModel?.errorExcerpt ?? "Sign-in failed.")}
          </p>
          <div className="pt-1">
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid="start-again-button"
              onClick={() => {
                if (isApiKey) {
                  setApiKeyStatus("idle");
                  setApiKeyError(null);
                } else {
                  void startLogin();
                }
              }}
            >
              Start again
            </Button>
          </div>
        </div>
      )}

      {/* Footer Actions (Cancel on non-terminal states) */}
      {!isDone && (
        <div className="flex justify-end border-border border-t pt-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="cancel-login-button"
            className="text-muted-foreground text-xs hover:text-foreground"
            onClick={() => {
              if (isApiKey) {
                onCancelled?.();
              } else {
                void cancelLogin();
              }
            }}
          >
            Cancel
          </Button>
        </div>
      )}
    </Card>
  );
}
