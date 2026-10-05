"use client";

import { useState } from "react";

import {
  Bot,
  CheckCircle2,
  Clock,
  MessageSquare,
  RotateCw,
  Search,
  Sliders,
  StopCircle,
  XCircle,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { submitCommand } from "@/lib/farm/actions";
import { fmtAbsolute, fmtInt } from "@/lib/farm/format";
import type { AiConversationItem, AiJobItem } from "@/lib/farm/integrations-data";
import type { Connection } from "@/lib/farm/types";

export function AiOrchestrationView({
  initialJobs,
  initialConversations,
  accounts,
}: {
  initialJobs: AiJobItem[];
  initialConversations: AiConversationItem[];
  accounts: Connection[];
}) {
  const [jobs, setJobs] = useState<AiJobItem[]>(initialJobs);
  const [conversations, _setConversations] = useState<AiConversationItem[]>(initialConversations);
  const [accountList, setAccountList] = useState<Connection[]>(accounts);

  const [activeTab, setActiveTab] = useState<"jobs" | "conversations" | "concurrency">("jobs");
  const [jobSearch, setJobSearch] = useState("");
  const [jobStateFilter, setJobStateFilter] = useState("all");
  const [cancellingId, setCancellingId] = useState<string | null>(null);

  // Filter jobs
  const filteredJobs = jobs.filter((j) => {
    if (jobStateFilter !== "all" && j.state !== jobStateFilter) return false;
    if (jobSearch) {
      const q = jobSearch.toLowerCase();
      return (
        j.task.toLowerCase().includes(q) ||
        j.id.toLowerCase().includes(q) ||
        j.account.toLowerCase().includes(q) ||
        j.ai.toLowerCase().includes(q)
      );
    }
    return true;
  });

  // Handle Cancel Job
  async function handleCancelJob(jobId: string) {
    setCancellingId(jobId);
    try {
      const res = await submitCommand("cancel_ai_job", { job_id: jobId });
      if (res.ok) {
        setJobs((prev) =>
          prev.map((j) =>
            j.id === jobId
              ? {
                  ...j,
                  state: "cancelled",
                  error: { kind: "cancelled", message: "Cancelled by owner via Console" },
                }
              : j,
          ),
        );
        toast.success(`AI job ${jobId} cancelled.`);
      } else {
        toast.error(res.error);
      }
    } catch {
      toast.error("Failed to cancel job.");
    } finally {
      setCancellingId(null);
    }
  }

  // Handle Update Concurrency (max_parallel)
  async function handleConcurrencyChange(accountId: string, maxParallel: number) {
    try {
      const res = await submitCommand("set_max_parallel", { connection_id: accountId, max_parallel: maxParallel });
      if (res.ok) {
        setAccountList((prev) => prev.map((acc) => (acc.id === accountId ? { ...acc, concurrency: maxParallel } : acc)));
        toast.success(`Concurrency for ${accountId} set to ${maxParallel}.`);
      } else {
        toast.error(res.error);
      }
    } catch {
      toast.error("Failed to update concurrency limit.");
    }
  }

  function renderJobStateBadge(state: AiJobItem["state"]) {
    switch (state) {
      case "running":
        return (
          <Badge className="animate-pulse gap-1 border-primary/30 bg-primary/15 font-medium text-[11px] text-primary">
            <RotateCw className="size-3 animate-spin" />
            <span>Running</span>
          </Badge>
        );
      case "queued":
        return (
          <Badge variant="outline" className="gap-1 font-medium text-[11px] text-muted-foreground">
            <Clock className="size-3" />
            <span>Queued</span>
          </Badge>
        );
      case "succeeded":
        return (
          <Badge className="gap-1 border-emerald-500/30 bg-emerald-500/15 font-medium text-[11px] text-emerald-700 dark:text-emerald-400">
            <CheckCircle2 className="size-3" />
            <span>Succeeded</span>
          </Badge>
        );
      case "failed":
        return (
          <Badge className="gap-1 border-destructive/30 bg-destructive/15 font-medium text-[11px] text-destructive">
            <XCircle className="size-3" />
            <span>Failed</span>
          </Badge>
        );
      case "cancelled":
        return (
          <Badge variant="secondary" className="gap-1 font-medium text-[11px] text-muted-foreground">
            <StopCircle className="size-3" />
            <span>Cancelled</span>
          </Badge>
        );
    }
  }

  return (
    <div className="space-y-6">
      {/* Navigation tabs */}
      <div className="flex items-center justify-between border-b pb-3">
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as typeof activeTab)}>
          <TabsList className="h-8">
            <TabsTrigger value="jobs" className="gap-1.5 px-3 text-xs">
              <Zap className="size-3.5" />
              <span>Live Jobs ({jobs.length})</span>
            </TabsTrigger>
            <TabsTrigger value="conversations" className="gap-1.5 px-3 text-xs">
              <MessageSquare className="size-3.5" />
              <span>Conversations ({conversations.length})</span>
            </TabsTrigger>
            <TabsTrigger value="concurrency" className="gap-1.5 px-3 text-xs">
              <Sliders className="size-3.5" />
              <span>Concurrency Limits</span>
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      {/* Tab 1: Live Jobs Table */}
      {activeTab === "jobs" && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
              <div>
                <CardTitle className="font-semibold text-base">Live AI Worker Jobs</CardTitle>
                <CardDescription className="text-xs">
                  Active and historical tasks dispatched to Claude, Codex, Gemini, and Hermes workers.
                </CardDescription>
              </div>

              <div className="flex items-center gap-2">
                <Select value={jobStateFilter} onValueChange={setJobStateFilter}>
                  <SelectTrigger className="h-8 w-[120px] text-xs">
                    <SelectValue placeholder="State" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all" className="text-xs">
                      All States
                    </SelectItem>
                    <SelectItem value="running" className="text-xs">
                      Running
                    </SelectItem>
                    <SelectItem value="queued" className="text-xs">
                      Queued
                    </SelectItem>
                    <SelectItem value="succeeded" className="text-xs">
                      Succeeded
                    </SelectItem>
                    <SelectItem value="cancelled" className="text-xs">
                      Cancelled
                    </SelectItem>
                  </SelectContent>
                </Select>

                <div className="relative w-48 sm:w-60">
                  <Search className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
                  <Input
                    placeholder="Search task or ID..."
                    value={jobSearch}
                    onChange={(e) => setJobSearch(e.target.value)}
                    className="h-8 pl-8 font-mono text-xs"
                  />
                </div>
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[130px]">State</TableHead>
                  <TableHead className="w-[110px]">Worker</TableHead>
                  <TableHead className="w-[140px]">Account</TableHead>
                  <TableHead>Task</TableHead>
                  <TableHead className="w-[90px]">Elapsed</TableHead>
                  <TableHead className="w-[110px]">Tokens / Cost</TableHead>
                  <TableHead className="w-[90px] text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredJobs.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="h-28 text-center text-muted-foreground text-xs">
                      No AI jobs matching the current filter.
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredJobs.map((job) => (
                    <TableRow key={job.id}>
                      <TableCell>{renderJobStateBadge(job.state)}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5 font-medium text-xs">
                          <Bot className="size-3.5 text-purple-600" />
                          <span className="capitalize">{job.ai}</span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{job.account}</span>
                      </TableCell>
                      <TableCell className="max-w-[320px]">
                        <div className="truncate font-medium text-xs">{job.task}</div>
                        {job.error && (
                          <div className="truncate text-[11px] text-destructive">Error: {job.error.message}</div>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-muted-foreground text-xs">{job.elapsedS}s</TableCell>
                      <TableCell className="text-xs">
                        <div>{fmtInt(job.tokens)} tok</div>
                        <div className="text-[11px] text-muted-foreground">${job.costUsd.toFixed(4)}</div>
                      </TableCell>
                      <TableCell className="text-right">
                        {job.state === "running" || job.state === "queued" ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 text-destructive text-xs hover:bg-destructive/10"
                            disabled={cancellingId === job.id}
                            onClick={() => handleCancelJob(job.id)}
                          >
                            <span>Cancel</span>
                          </Button>
                        ) : (
                          <span className="text-muted-foreground text-xs">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Tab 2: Conversations List (Metadata only, no transcripts) */}
      {activeTab === "conversations" && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="font-semibold text-base">Active AI Conversations</CardTitle>
            <CardDescription className="text-xs">
              Persistent worker sessions preserving multi-turn context (metadata only, no transcripts stored).
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[120px]">Conversation</TableHead>
                  <TableHead className="w-[110px]">AI Worker</TableHead>
                  <TableHead className="w-[140px]">Account</TableHead>
                  <TableHead className="w-[180px]">Native Session ID</TableHead>
                  <TableHead className="w-[90px]">Turns</TableHead>
                  <TableHead className="w-[110px]">Tokens</TableHead>
                  <TableHead className="w-[90px]">Cost</TableHead>
                  <TableHead className="text-right">Last Activity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {conversations.map((conv) => (
                  <TableRow key={conv.id}>
                    <TableCell className="font-mono font-semibold text-primary text-xs">{conv.id}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5 font-medium text-xs capitalize">
                        <Bot className="size-3.5 text-purple-600" />
                        <span>{conv.ai}</span>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{conv.account}</TableCell>
                    <TableCell className="max-w-[180px] truncate font-mono text-muted-foreground text-xs">
                      {conv.nativeSessionId}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{conv.turns} turns</TableCell>
                    <TableCell className="font-mono text-xs">{fmtInt(conv.tokens)}</TableCell>
                    <TableCell className="font-medium font-mono text-xs">${conv.costUsd.toFixed(3)}</TableCell>
                    <TableCell className="text-right text-[11px] text-muted-foreground">
                      {fmtAbsolute(conv.updatedAt)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Tab 3: Per-Account Concurrency Limits (max_parallel) */}
      {activeTab === "concurrency" && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="font-semibold text-base">Account Concurrency Control</CardTitle>
            <CardDescription className="text-xs">
              Configure maximum parallel jobs (meta.max_parallel) allowed per AI account to prevent rate-limit crashes.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Account ID</TableHead>
                  <TableHead>Account Label</TableHead>
                  <TableHead>AI Provider</TableHead>
                  <TableHead>Auth Ref / Dir</TableHead>
                  <TableHead className="w-[150px] text-right">Max Parallel Jobs</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accountList.map((acc) => (
                  <TableRow key={acc.id}>
                    <TableCell className="font-mono font-semibold text-xs">{acc.id}</TableCell>
                    <TableCell className="font-medium text-xs">{acc.label}</TableCell>
                    <TableCell className="text-xs capitalize">{acc.providerName}</TableCell>
                    <TableCell className="max-w-[220px] truncate font-mono text-muted-foreground text-xs">
                      {acc.authRef}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1.5">
                        <Select
                          value={String(acc.concurrency)}
                          onValueChange={(val) => handleConcurrencyChange(acc.id, Number.parseInt(val, 10))}
                        >
                          <SelectTrigger className="h-7 w-24 font-mono text-xs">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="1" className="font-mono text-xs">
                              1 job
                            </SelectItem>
                            <SelectItem value="2" className="font-mono text-xs">
                              2 jobs
                            </SelectItem>
                            <SelectItem value="4" className="font-mono text-xs">
                              4 jobs
                            </SelectItem>
                            <SelectItem value="8" className="font-mono text-xs">
                              8 jobs
                            </SelectItem>
                          </SelectContent>
                        </Select>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
