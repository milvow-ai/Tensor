"use client";

import type React from "react";
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

import { StatusChip } from "@/components/kit";
import { RelativeTime } from "@/components/farm/time";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DASH, fmtAbsolute, fmtDuration, fmtUsd } from "@/lib/farm/format";
import { redactJson } from "@/lib/farm/redact";
import type { RunDetailRow, RunEventKind } from "@/lib/farm/types";

import { KIT_CARD, KIT_TEXT_PRIMARY, KIT_TEXT_SECONDARY, RUN_CHIP } from "./run-status";

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

function Section({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className={`${KIT_CARD} p-4 sm:p-5`}>
      <div className="mb-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className={`font-semibold text-sm ${KIT_TEXT_PRIMARY}`}>{title}</h2>
          {description ? <p className={`mt-0.5 text-xs ${KIT_TEXT_SECONDARY}`}>{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function RunDetailView({ run }: { run: RunDetailRow }) {
  const [jsonOpen, setJsonOpen] = useState(true);
  const [copied, setCopied] = useState(false);
  const chip = RUN_CHIP[run.status];

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
    <div className="flex flex-col gap-4 md:gap-5">
      {/* Summary */}
      <Section title="Summary" description="Outcome and routing metadata recorded by the Farm.">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className={`font-mono font-semibold text-base ${KIT_TEXT_PRIMARY}`}>{run.capability}</span>
            <StatusChip size="sm" status={chip.status} label={chip.label} />
            {run.cached ? <span className={`text-xs ${KIT_TEXT_SECONDARY}`}>Cached</span> : null}
          </div>
          <div className={`text-xs ${KIT_TEXT_SECONDARY}`}>
            Started <RelativeTime iso={run.started_at} /> via <span className="font-medium font-mono">{run.caller}</span>
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-4">
          <div>
            <dt className={KIT_TEXT_SECONDARY}>Pool</dt>
            <dd className="mt-0.5 font-medium">{run.provider_name ?? (run.cached ? "Cache" : DASH)}</dd>
          </div>
          <div>
            <dt className={KIT_TEXT_SECONDARY}>Account</dt>
            <dd className="mt-0.5 font-mono">
              {run.connection_id ? (
                <span>
                  {run.connection_label ?? run.connection_id}
                  <span className={`block font-mono text-[11px] ${KIT_TEXT_SECONDARY}`}>({run.connection_id})</span>
                </span>
              ) : (
                DASH
              )}
            </dd>
          </div>
          <div>
            <dt className={KIT_TEXT_SECONDARY}>Strategy / Attempts</dt>
            <dd className="mt-0.5 font-mono">
              {run.strategy ?? "failover"} · {run.attempts_count} attempt{run.attempts_count === 1 ? "" : "s"}
            </dd>
          </div>
          <div>
            <dt className={KIT_TEXT_SECONDARY}>Cost / Duration</dt>
            <dd className="mt-0.5 font-mono tabular-nums">
              {fmtUsd(run.cost_usd)} · {fmtDuration(run.duration_ms)}
            </dd>
          </div>
        </dl>

        {run.error_kind ? (
          <div className="mt-4 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-negative-soft,#fee2e2)] p-3 text-xs dark:bg-[rgba(248,81,73,0.15)]">
            <div className="font-mono font-semibold text-[var(--kit-negative-text,#b91c1c)] dark:text-[#ff7b72]">
              {run.error_kind}
            </div>
            {run.error ? <p className={`mt-1 ${KIT_TEXT_SECONDARY}`}>{run.error}</p> : null}
          </div>
        ) : null}
      </Section>

      {/* Trace */}
      <Section
        title="Trace"
        description="Request, policy, candidates, reserve, execute and commit, with the time between steps."
      >
        {run.events.length === 0 ? (
          <p className={`py-4 text-center text-xs ${KIT_TEXT_SECONDARY}`}>No event trajectory recorded for this run.</p>
        ) : (
          <div className="relative pl-6 before:absolute before:top-2 before:bottom-2 before:left-[11px] before:w-0.5 before:bg-[var(--kit-border,#e6e8ec)] dark:before:bg-[#30363d]">
            {run.events.map((evt, idx) => {
              const prevEvt = idx > 0 ? run.events[idx - 1] : null;
              const stepLatency = prevEvt
                ? Math.max(0, new Date(evt.at).getTime() - new Date(prevEvt.at).getTime())
                : null;

              const isFallback = evt.kind === "fallback";

              return (
                <div key={evt.id} className="relative pb-6 last:pb-1" data-testid={`event-step-${evt.kind}`}>
                  <div className="absolute top-0 -left-6 flex size-5 items-center justify-center rounded-full border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] dark:border-[#30363d] dark:bg-[#161b22]">
                    <EventIcon kind={evt.kind} />
                  </div>

                  <div className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className={`font-semibold text-xs capitalize tracking-tight ${KIT_TEXT_PRIMARY}`}>
                          {evt.kind.replace("_", " ")}
                        </span>
                        {evt.connection_id ? (
                          <span className={`font-mono text-[11px] ${KIT_TEXT_SECONDARY}`}>({evt.connection_id})</span>
                        ) : null}
                      </div>
                      <div className={`flex items-center gap-2 text-[11px] tabular-nums ${KIT_TEXT_SECONDARY}`}>
                        {stepLatency !== null && stepLatency > 0 ? (
                          <span className="rounded bg-[var(--kit-bg-subtle,#f3f4f6)] px-1.5 py-0.5 font-mono dark:bg-[#21262d]">
                            +{stepLatency}ms
                          </span>
                        ) : null}
                        <span>{fmtAbsolute(evt.at, true)}</span>
                      </div>
                    </div>

                    {isFallback ? (
                      <div
                        className="mt-1 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-warning-soft,#fef3c7)] p-2.5 text-[var(--kit-warning-text,#b45309)] text-xs dark:bg-[rgba(210,153,34,0.15)] dark:text-[#e3b341]"
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

                    {!isFallback && evt.data && Object.keys(evt.data).length > 0 ? (
                      <div
                        className={`mt-0.5 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-2 font-mono text-[11px] dark:bg-[#21262d] ${KIT_TEXT_SECONDARY}`}
                      >
                        {evt.data.reason ? (
                          <p className={KIT_TEXT_PRIMARY}>{String(evt.data.reason)}</p>
                        ) : (
                          <pre className="overflow-x-auto whitespace-pre-wrap">{JSON.stringify(evt.data, null, 2)}</pre>
                        )}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {/* Result envelope */}
      <Collapsible open={jsonOpen} onOpenChange={setJsonOpen}>
        <Section
          title="Result Envelope"
          description="Collapsible JSON viewer with secrets redacted by construction."
          actions={
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" className="h-7 gap-1.5 text-xs" onClick={copyJson}>
                {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                <span>{copied ? "Copied" : "Copy JSON"}</span>
              </Button>
              <CollapsibleTrigger asChild>
                <Button size="icon-sm" variant="ghost" aria-label={jsonOpen ? "Collapse envelope" : "Expand envelope"}>
                  {jsonOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                </Button>
              </CollapsibleTrigger>
            </div>
          }
        >
          <CollapsibleContent>
            <div className="relative rounded-[var(--kit-radius-control,8px)] bg-zinc-950 p-4 text-zinc-100 dark:bg-zinc-900">
              <pre className="max-h-96 overflow-auto font-mono text-xs leading-relaxed">{jsonString}</pre>
            </div>
          </CollapsibleContent>
        </Section>
      </Collapsible>

      {/* Evidence */}
      <Section title="Evidence & Artifacts" description="Thumbnails and captured artifacts stored on Supabase Storage or Farm PC.">
        {run.evidence_ids.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {run.evidence_ids.map((id) => (
              <div
                key={id}
                className="flex flex-col gap-2 rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] p-3 dark:border-[#30363d]"
              >
                <div className="flex items-center justify-between text-xs">
                  <span className={`font-mono ${KIT_TEXT_SECONDARY}`}>{id.slice(0, 8)}</span>
                  <StatusChip size="sm" status="done" label="Captured" />
                </div>
                <div className="flex h-32 items-center justify-center rounded border border-[var(--kit-border,#e6e8ec)] border-dashed bg-[var(--kit-bg-subtle,#f3f4f6)] dark:border-[#30363d] dark:bg-[#21262d]">
                  <div className={`flex flex-col items-center gap-1.5 text-xs ${KIT_TEXT_SECONDARY}`}>
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
          <div
            className={`flex flex-col items-center justify-center gap-2 rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] border-dashed py-8 text-center dark:border-[#30363d] ${KIT_TEXT_SECONDARY}`}
          >
            <HardDrive className="size-7 opacity-50" />
            <div className="font-medium text-xs">Evidence on the Farm PC</div>
            <p className="max-w-xs text-[11px]">
              No cloud storage thumbnail was captured for this capability. Originals remain stored locally on the Farm
              PC.
            </p>
          </div>
        )}
      </Section>
    </div>
  );
}
