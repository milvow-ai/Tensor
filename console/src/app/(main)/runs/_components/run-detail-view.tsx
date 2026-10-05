"use client";

import { useState } from "react";

import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  HardDrive,
  ImageIcon,
  Lock,
  Play,
  RotateCcw,
  ShieldAlert,
  Shuffle,
  Unlock,
  XCircle,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { ToneBadge } from "@/components/farm/status";
import { RelativeTime } from "@/components/farm/time";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DASH, fmtAbsolute, fmtDuration, fmtUsd } from "@/lib/farm/format";
import { redactJson } from "@/lib/farm/redact";
import { RUN_STATUS_META } from "@/lib/farm/state";
import type { RunDetailRow, RunEventKind } from "@/lib/farm/types";

function EventIcon({ kind }: { kind: RunEventKind }) {
  switch (kind) {
    case "plan":
      return <Play className="size-3.5 text-blue-500" />;
    case "candidate":
      return <ArrowRight className="size-3.5 text-muted-foreground" />;
    case "skip":
      return <RotateCcw className="size-3.5 text-amber-500" />;
    case "reserve":
      return <Lock className="size-3.5 text-indigo-500" />;
    case "execute":
      return <Play className="size-3.5 text-sky-500" />;
    case "success":
      return <CheckCircle2 className="size-3.5 text-emerald-500" />;
    case "failure":
      return <XCircle className="size-3.5 text-rose-500" />;
    case "fallback":
      return <Shuffle className="size-3.5 text-amber-500" />;
    case "commit":
      return <Check className="size-3.5 text-emerald-600" />;
    case "release":
      return <Unlock className="size-3.5 text-muted-foreground" />;
    case "cache_hit":
      return <Zap className="size-3.5 text-amber-400" />;
    case "policy_block":
      return <ShieldAlert className="size-3.5 text-rose-500" />;
    default:
      return <ArrowRight className="size-3.5 text-muted-foreground" />;
  }
}

export function RunDetailView({ run }: { run: RunDetailRow }) {
  const [jsonOpen, setJsonOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const meta = RUN_STATUS_META[run.status];

  // Redact any potential keys in result / params envelope
  const redactedEnvelope = {
    request_id: run.request_id,
    capability: run.capability,
    caller: run.caller,
    params: redactJson(run.params),
    result: redactJson(run.result),
    error: run.error_kind ? { kind: run.error_kind, message: run.error } : null,
    cost: {
      usd: run.cost_usd,
      cached: run.cached,
    },
  };

  const jsonString = JSON.stringify(redactedEnvelope, null, 2);

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(jsonString);
      setCopied(true);
      toast.success("Result envelope copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Failed to copy JSON");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Run Summary Card */}
      <Card>
        <CardHeader className="pb-3">
          <h2 className="mb-1 font-semibold text-muted-foreground text-xs uppercase tracking-wider">Summary</h2>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <CardTitle className="font-mono text-base">{run.capability}</CardTitle>
              <ToneBadge tone={meta.tone}>{meta.label}</ToneBadge>
              {run.cached ? <ToneBadge tone="info">Cached</ToneBadge> : null}
            </div>
            <div className="text-muted-foreground text-xs">
              Started <RelativeTime iso={run.started_at} /> via{" "}
              <span className="font-medium font-mono">{run.caller}</span>
            </div>
          </div>
          <CardDescription>Outcome and routing metadata recorded by the Farm.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-4">
            <div>
              <dt className="text-muted-foreground">Pool</dt>
              <dd className="mt-0.5 font-medium">{run.provider_name ?? (run.cached ? "Cache" : DASH)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Account</dt>
              <dd className="mt-0.5 font-mono">
                {run.connection_id ? (
                  <span>
                    {run.connection_label ?? run.connection_id}
                    <span className="block font-mono text-[11px] text-muted-foreground">({run.connection_id})</span>
                  </span>
                ) : (
                  DASH
                )}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Strategy / Attempts</dt>
              <dd className="mt-0.5 font-mono">
                {run.strategy ?? "failover"} · {run.attempts_count} attempt{run.attempts_count === 1 ? "" : "s"}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Cost / Duration</dt>
              <dd className="mt-0.5 font-mono tabular-nums">
                {fmtUsd(run.cost_usd)} · {fmtDuration(run.duration_ms)}
              </dd>
            </div>
          </dl>

          {run.error_kind ? (
            <div className="mt-4 rounded-lg border border-amber-600/25 bg-amber-500/10 p-3 text-xs">
              <div className="font-mono font-semibold text-amber-800 dark:text-amber-400">{run.error_kind}</div>
              {run.error ? <p className="mt-1 text-muted-foreground">{run.error}</p> : null}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Trajectory Timeline Stepper */}
      <Card>
        <CardHeader>
          <CardTitle className="font-semibold text-sm">Event Trajectory Timeline</CardTitle>
          <CardDescription>
            Step-by-step decisions: planning, candidate ranking, reservations, execution, fallbacks and commits.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {run.events.length === 0 ? (
            <p className="py-4 text-center text-muted-foreground text-xs">No event trajectory recorded for this run.</p>
          ) : (
            <div className="relative pl-6 before:absolute before:top-2 before:bottom-2 before:left-[11px] before:w-0.5 before:bg-border">
              {run.events.map((evt, idx) => {
                const prevEvt = idx > 0 ? run.events[idx - 1] : null;
                const stepLatency = prevEvt
                  ? Math.max(0, new Date(evt.at).getTime() - new Date(prevEvt.at).getTime())
                  : null;

                const isFallback = evt.kind === "fallback";

                return (
                  <div key={evt.id} className="relative pb-6 last:pb-1" data-testid={`event-step-${evt.kind}`}>
                    {/* Stepper Dot */}
                    <div className="absolute top-0 -left-6 flex size-5 items-center justify-center rounded-full border bg-background shadow-xs">
                      <EventIcon kind={evt.kind} />
                    </div>

                    <div className="flex flex-col gap-1">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-xs capitalize tracking-tight">
                            {evt.kind.replace("_", " ")}
                          </span>
                          {evt.connection_id ? (
                            <span className="font-mono text-[11px] text-muted-foreground">({evt.connection_id})</span>
                          ) : null}
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-muted-foreground tabular-nums">
                          {stepLatency !== null && stepLatency > 0 ? (
                            <span className="rounded bg-muted px-1.5 py-0.2 font-mono">+{stepLatency}ms</span>
                          ) : null}
                          <span>{fmtAbsolute(evt.at, true)}</span>
                        </div>
                      </div>

                      {/* Fallback highlight */}
                      {isFallback ? (
                        <div
                          className="mt-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-2.5 text-amber-800 text-xs dark:text-amber-300"
                          data-testid="fallback-reason-block"
                        >
                          <div className="flex items-center gap-1.5 font-semibold">
                            <Shuffle className="size-3.5 shrink-0" />
                            <span>Fallback Triggered</span>
                          </div>
                          <p className="mt-1 font-mono text-[11px]">
                            {String(
                              evt.data?.reason ?? "Rate limit or connection error; falling back to next provider.",
                            )}
                          </p>
                          {evt.data?.from && evt.data?.to ? (
                            <div className="mt-1.5 flex items-center gap-1 text-[11px] opacity-80">
                              <span className="font-mono">{String(evt.data.from)}</span>
                              <ArrowRight className="size-3" />
                              <span className="font-medium font-mono">{String(evt.data.to)}</span>
                            </div>
                          ) : null}
                        </div>
                      ) : null}

                      {/* Generic Event Data */}
                      {!isFallback && evt.data && Object.keys(evt.data).length > 0 ? (
                        <div className="mt-0.5 rounded bg-muted/40 p-2 font-mono text-[11px] text-muted-foreground">
                          {evt.data.reason ? (
                            <p className="text-foreground">{String(evt.data.reason)}</p>
                          ) : (
                            <pre className="overflow-x-auto whitespace-pre-wrap">
                              {JSON.stringify(evt.data, null, 2)}
                            </pre>
                          )}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Result Envelope Viewer */}
      <Card>
        <Collapsible open={jsonOpen} onOpenChange={setJsonOpen}>
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <div>
              <CardTitle className="font-semibold text-sm">Result Envelope</CardTitle>
              <CardDescription>Collapsible JSON viewer with secrets redacted by construction.</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" onClick={copyJson}>
                {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                <span>{copied ? "Copied" : "Copy JSON"}</span>
              </Button>
              <CollapsibleTrigger asChild>
                <Button size="icon-sm" variant="ghost">
                  {jsonOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                </Button>
              </CollapsibleTrigger>
            </div>
          </CardHeader>
          <CollapsibleContent>
            <CardContent>
              <div className="relative rounded-lg border bg-zinc-950 p-4 text-zinc-100 dark:bg-zinc-900">
                <pre className="max-h-96 overflow-auto font-mono text-xs leading-relaxed">{jsonString}</pre>
              </div>
            </CardContent>
          </CollapsibleContent>
        </Collapsible>
      </Card>

      {/* Evidence Viewer */}
      <Card>
        <CardHeader>
          <CardTitle className="font-semibold text-sm">Evidence & Artifacts</CardTitle>
          <CardDescription>Thumbnails and captured artifacts stored on Supabase Storage or Farm PC.</CardDescription>
        </CardHeader>
        <CardContent>
          {run.evidence_ids.length > 0 ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {run.evidence_ids.map((id) => (
                <div key={id} className="flex flex-col gap-2 rounded-lg border p-3">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono text-muted-foreground">{id.slice(0, 8)}</span>
                    <ToneBadge tone="ok">Captured</ToneBadge>
                  </div>
                  <div className="flex h-32 items-center justify-center rounded border border-dashed bg-muted/30">
                    <div className="flex flex-col items-center gap-1.5 text-muted-foreground text-xs">
                      <ImageIcon className="size-6 opacity-60" />
                      <span>Evidence thumbnail</span>
                    </div>
                  </div>
                  <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs" asChild>
                    <a href={`/evidence/${id}`} target="_blank" rel="noopener noreferrer">
                      <span>View original capture</span>
                      <ExternalLink className="size-3" />
                    </a>
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed py-8 text-center text-muted-foreground">
              <HardDrive className="size-7 opacity-50" />
              <div className="font-medium text-xs">Evidence on the Farm PC</div>
              <p className="max-w-xs text-[11px] text-muted-foreground/80">
                No cloud storage thumbnail was captured for this capability. Originals remain stored locally on the Farm
                PC.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
