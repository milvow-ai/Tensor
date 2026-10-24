"use client";

import { useEffect, useState } from "react";

import { AlertCircle, Check, Lock, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { submitCommand } from "@/lib/farm/actions";
import { generateEnvName } from "@/lib/farm/login";
import { readConnectPublicKey } from "@/lib/farm/login-actions";
import { buildAad, seal } from "@/lib/farm/seal";
import type { ConnectPublicKeyJwk } from "@/lib/farm/types";

export interface ApiKeyFormProps {
  providerId: string;
  connectionId?: string;
  existingNames?: string[];
  onSuccess?: (results: KeyResult[]) => void;
  className?: string;
}

export interface KeyResult {
  name: string;
  ok: boolean;
  error?: string;
}

export function ApiKeyForm({
  providerId,
  connectionId,
  existingNames = [],
  onSuccess,
  className = "",
}: ApiKeyFormProps) {
  const [publicKey, setPublicKey] = useState<ConnectPublicKeyJwk | null>(null);
  const [loadingKey, setLoadingKey] = useState<boolean>(true);

  const [mode, setMode] = useState<"single" | "bulk">("single");
  const [singleKey, setSingleKey] = useState("");
  const [bulkKeys, setBulkKeys] = useState("");
  const [customEnvName, setCustomEnvName] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState<KeyResult[]>([]);

  // Load connect public key
  useEffect(() => {
    let active = true;
    async function loadKey() {
      try {
        const pk = await readConnectPublicKey();
        if (active) {
          setPublicKey(pk);
        }
      } catch (err) {
        if (active) {
          console.error("Failed to read connect public key", err);
        }
      } finally {
        if (active) {
          setLoadingKey(false);
        }
      }
    }
    void loadKey();
    return () => {
      active = false;
    };
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!publicKey) {
      toast.error("Start the Farm to sign accounts in");
      return;
    }

    const rawKeys: string[] =
      mode === "single"
        ? [singleKey.trim()]
        : bulkKeys
            .split("\n")
            .map((k) => k.trim())
            .filter(Boolean);

    const validKeys = rawKeys.filter(Boolean);
    if (validKeys.length === 0) {
      toast.error("Please enter at least one API key");
      return;
    }

    // Immediately clear plaintext from state
    setSingleKey("");
    setBulkKeys("");
    setSubmitting(true);

    const usedNames = new Set<string>(existingNames);
    for (const r of results) {
      usedNames.add(r.name);
    }

    const newResults: KeyResult[] = [];

    for (let i = 0; i < validKeys.length; i++) {
      const plainKey = validKeys[i];
      const envName =
        mode === "single" && customEnvName.trim()
          ? customEnvName.trim().toUpperCase()
          : generateEnvName(providerId, usedNames);

      usedNames.add(envName);

      try {
        const aad = buildAad(envName, "secret");
        const envelope = await seal(plainKey, publicKey, aad);

        const res = await submitCommand("save_secret", {
          name: envName,
          sealed: envelope,
          connection_id: connectionId,
        });

        if (res.ok) {
          newResults.push({ name: envName, ok: true });
        } else {
          newResults.push({ name: envName, ok: false, error: res.error });
        }
      } catch (err) {
        newResults.push({
          name: envName,
          ok: false,
          error: err instanceof Error ? err.message : "Encryption or network error",
        });
      }
    }

    setResults((prev) => [...newResults, ...prev]);
    setSubmitting(false);

    const successCount = newResults.filter((r) => r.ok).length;
    if (successCount > 0) {
      toast.success(`Saved ${successCount} sealed secret${successCount > 1 ? "s" : ""}`);
    }
    if (newResults.some((r) => !r.ok)) {
      toast.error("Some secrets failed to save");
    }

    onSuccess?.(newResults);
  };

  const isDisabled = !publicKey || loadingKey;

  return (
    <Card data-testid="api-key-form" className={`space-y-4 rounded-xl border border-border bg-card p-5 ${className}`}>
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-semibold text-foreground text-sm tracking-tight">Save API Keys</h3>
          <p className="mt-0.5 text-muted-foreground text-xs">
            Keys are encrypted in this browser using WebCrypto and saved to the Farm vault.
          </p>
        </div>
        <div className="flex items-center gap-1.5 rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1 font-medium text-[11px] text-emerald-700 dark:text-emerald-400">
          <Lock className="size-3" />
          <span>Encrypted in this browser for your Farm</span>
        </div>
      </div>

      {isDisabled && !loadingKey && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-amber-700 text-xs dark:text-amber-400">
          Start the Farm to sign accounts in (encryption key is currently unavailable).
        </div>
      )}

      <Tabs value={mode} onValueChange={(v) => setMode(v as "single" | "bulk")}>
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="single">Paste API key</TabsTrigger>
          <TabsTrigger value="bulk">Paste keys, one per line</TabsTrigger>
        </TabsList>

        <form onSubmit={handleSave} className="space-y-4 pt-3">
          <TabsContent value="single" className="mt-0 space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="single-key-input" className="font-medium text-xs">
                API Key
              </Label>
              <Input
                id="single-key-input"
                data-testid="api-key-single-input"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder="sk-..."
                value={singleKey}
                onChange={(e) => setSingleKey(e.target.value)}
                disabled={isDisabled || submitting}
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="custom-env-name" className="font-medium text-muted-foreground text-xs">
                Env Name (optional override)
              </Label>
              <Input
                id="custom-env-name"
                data-testid="custom-env-name"
                placeholder={generateEnvName(providerId, existingNames)}
                value={customEnvName}
                onChange={(e) => setCustomEnvName(e.target.value)}
                disabled={isDisabled || submitting}
                className="font-mono text-xs"
              />
            </div>
          </TabsContent>

          <TabsContent value="bulk" className="mt-0 space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="bulk-keys-input" className="font-medium text-xs">
                Paste keys (one per line)
              </Label>
              <Textarea
                id="bulk-keys-input"
                data-testid="api-key-bulk-input"
                autoComplete="off"
                spellCheck={false}
                rows={4}
                placeholder="key-1&#10;key-2&#10;key-3"
                value={bulkKeys}
                onChange={(e) => setBulkKeys(e.target.value)}
                disabled={isDisabled || submitting}
                className="font-mono text-xs"
              />
              <p className="text-[11px] text-muted-foreground">
                Each key will be sealed and saved under a unique variable name (e.g.{" "}
                <span className="font-mono">{generateEnvName(providerId, existingNames)}</span>).
              </p>
            </div>
          </TabsContent>

          <div className="flex items-center justify-between pt-1">
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <ShieldCheck className="size-3.5 text-primary" />
              <span>Never sent or stored in plain text</span>
            </div>
            <Button
              type="submit"
              size="sm"
              data-testid="save-secret-button"
              disabled={isDisabled || submitting || (mode === "single" ? !singleKey.trim() : !bulkKeys.trim())}
            >
              {submitting ? <Spinner className="mr-2 size-4" /> : null}
              {mode === "single" ? "Seal & save key" : "Seal & save keys"}
            </Button>
          </div>
        </form>
      </Tabs>

      {/* Results table */}
      {results.length > 0 && (
        <div className="space-y-2 border-border border-t pt-4">
          <h4 className="font-medium text-foreground text-xs">Saved Secrets</h4>
          <div className="space-y-1.5">
            {results.map((r) => (
              <div
                key={r.name}
                data-testid="secret-result-row"
                className="flex items-center justify-between rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs"
              >
                <div className="flex items-center gap-2">
                  {r.ok ? (
                    <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" />
                  ) : (
                    <AlertCircle className="size-3.5 text-destructive" />
                  )}
                  <span className="font-medium font-mono">{r.name}</span>
                </div>
                <div>
                  {r.ok ? (
                    <span className="font-medium text-[11px] text-emerald-600 dark:text-emerald-400">Saved</span>
                  ) : (
                    <span className="text-[11px] text-destructive">{r.error ?? "Failed"}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
