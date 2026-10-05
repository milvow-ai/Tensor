"use client";

import { useState } from "react";
import Link from "next/link";

import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Copy,
  Database,
  ExternalLink,
  Eye,
  Globe,
  Image as ImageIcon,
  Search,
  Shield,
  User,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/farm/states";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { fmtAbsolute } from "@/lib/farm/format";
import type { EvidenceRow, FactRow } from "@/lib/farm/types";

export function MemoryView({
  initialFacts,
  initialEvidence,
}: {
  initialFacts: FactRow[];
  initialEvidence: EvidenceRow[];
}) {
  const [activeTab, setActiveTab] = useState<"facts" | "evidence">("facts");
  const [search, setSearch] = useState("");
  const [freshnessFilter, setFreshnessFilter] = useState<string>("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceRow | null>(null);

  // Filter facts
  const filteredFacts = initialFacts.filter((fact) => {
    if (freshnessFilter !== "all" && fact.freshness_state !== freshnessFilter) return false;
    if (kindFilter !== "all" && fact.entity_kind !== kindFilter) return false;
    if (search) {
      const q = search.toLowerCase();
      const matchName = fact.entity_name.toLowerCase().includes(q);
      const matchKey = fact.entity_canonical_key.toLowerCase().includes(q);
      const matchAttr = fact.attribute.toLowerCase().includes(q);
      const matchVal =
        typeof fact.value === "string"
          ? fact.value.toLowerCase().includes(q)
          : JSON.stringify(fact.value).toLowerCase().includes(q);
      if (!matchName && !matchKey && !matchAttr && !matchVal) return false;
    }
    return true;
  });

  // Filter evidence
  const filteredEvidence = initialEvidence.filter((ev) => {
    if (search) {
      const q = search.toLowerCase();
      const matchSha = ev.sha256.toLowerCase().includes(q);
      const matchUrl = ev.url?.toLowerCase().includes(q);
      const matchPath = ev.path.toLowerCase().includes(q);
      if (!matchSha && !matchUrl && !matchPath) return false;
    }
    return true;
  });

  function copyText(text: string, label: string) {
    navigator.clipboard.writeText(text);
    toast.success(`${label} copied to clipboard.`);
  }

  function renderFreshnessBadge(state: FactRow["freshness_state"]) {
    switch (state) {
      case "fresh":
        return (
          <Badge className="gap-1 border-emerald-500/30 bg-emerald-500/15 font-medium text-[11px] text-emerald-700 dark:text-emerald-400">
            <CheckCircle2 className="size-3" />
            <span>Fresh</span>
          </Badge>
        );
      case "stale":
        return (
          <Badge className="gap-1 border-amber-500/30 bg-amber-500/15 font-medium text-[11px] text-amber-700 dark:text-amber-400">
            <Clock className="size-3" />
            <span>Stale</span>
          </Badge>
        );
      case "expired":
        return (
          <Badge className="gap-1 border-destructive/30 bg-destructive/15 font-medium text-[11px] text-destructive">
            <AlertTriangle className="size-3" />
            <span>Expired</span>
          </Badge>
        );
    }
  }

  function openEvidenceForFact(evidenceIds: string[]) {
    if (evidenceIds.length === 0) {
      toast.info("No captured evidence files associated with this fact.");
      return;
    }
    const found = initialEvidence.find((e) => evidenceIds.includes(e.id));
    if (found) {
      setSelectedEvidence(found);
    } else {
      toast.info(`Evidence artifact (${evidenceIds[0]}) not found in current view.`);
    }
  }

  if (initialFacts.length === 0 && initialEvidence.length === 0) {
    return (
      <EmptyState
        icon={Database}
        title="No memory or evidence yet"
        description="Verified entity facts, provenance, and captured evidence artifacts will appear here as tools and AI workflows run."
      >
        <Button asChild>
          <Link href="/integrations">Add your first MCP server</Link>
        </Button>
      </EmptyState>
    );
  }

  return (
    <div className="space-y-6">
      {/* Top filters bar */}
      <div className="flex flex-col items-stretch justify-between gap-3 sm:flex-row sm:items-center">
        <div className="relative max-w-md flex-1">
          <Search className="absolute top-2.5 left-2.5 size-4 text-muted-foreground" />
          <Input
            placeholder="Search company domain, person, or attribute..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 font-mono text-xs"
            aria-label="Search memory and evidence"
          />
        </div>

        <div className="flex items-center gap-2">
          {activeTab === "facts" && (
            <>
              <Select value={freshnessFilter} onValueChange={setFreshnessFilter}>
                <SelectTrigger className="h-8 w-[125px] text-xs">
                  <SelectValue placeholder="Freshness" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all" className="text-xs">
                    All Freshness
                  </SelectItem>
                  <SelectItem value="fresh" className="text-xs">
                    Fresh
                  </SelectItem>
                  <SelectItem value="stale" className="text-xs">
                    Stale (&gt;30d)
                  </SelectItem>
                  <SelectItem value="expired" className="text-xs">
                    Expired
                  </SelectItem>
                </SelectContent>
              </Select>

              <Select value={kindFilter} onValueChange={setKindFilter}>
                <SelectTrigger className="h-8 w-[120px] text-xs">
                  <SelectValue placeholder="Kind" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all" className="text-xs">
                    All Kinds
                  </SelectItem>
                  <SelectItem value="company" className="text-xs">
                    Company
                  </SelectItem>
                  <SelectItem value="person" className="text-xs">
                    Person
                  </SelectItem>
                </SelectContent>
              </Select>
            </>
          )}

          <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as "facts" | "evidence")}>
            <TabsList className="h-8">
              <TabsTrigger value="facts" className="px-3 text-xs">
                Facts ({filteredFacts.length})
              </TabsTrigger>
              <TabsTrigger value="evidence" className="px-3 text-xs">
                Evidence ({filteredEvidence.length})
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      {/* Facts View */}
      {activeTab === "facts" && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="font-semibold text-base">Knowledge Base Facts</CardTitle>
            <CardDescription className="text-xs">
              Every verified attribute the Farm knows, with source provenance, confidence, and freshness.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[200px]">Entity</TableHead>
                  <TableHead className="w-[150px]">Attribute</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead className="w-[100px]">Freshness</TableHead>
                  <TableHead className="w-[140px]">Source</TableHead>
                  <TableHead className="w-[120px]">Observed</TableHead>
                  <TableHead className="w-[80px]">Confidence</TableHead>
                  <TableHead className="w-[90px] text-right">Proof</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredFacts.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="h-28 text-center text-muted-foreground text-xs">
                      No facts found matching search criteria.
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredFacts.map((fact) => (
                    <TableRow key={fact.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {fact.entity_kind === "company" ? (
                            <Globe className="size-3.5 shrink-0 text-muted-foreground" />
                          ) : (
                            <User className="size-3.5 shrink-0 text-muted-foreground" />
                          )}
                          <div className="truncate">
                            <div className="font-medium text-xs">{fact.entity_name}</div>
                            <div className="font-mono text-[11px] text-muted-foreground">
                              {fact.entity_canonical_key}
                            </div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell className="font-mono font-semibold text-primary text-xs">{fact.attribute}</TableCell>
                      <TableCell className="max-w-[280px]">
                        <div className="truncate rounded bg-muted/40 px-2 py-1 font-mono text-xs">
                          {typeof fact.value === "object" ? JSON.stringify(fact.value) : String(fact.value)}
                        </div>
                      </TableCell>
                      <TableCell>{renderFreshnessBadge(fact.freshness_state)}</TableCell>
                      <TableCell className="text-xs">
                        <span className="font-mono text-[11px] text-muted-foreground">{fact.source_label}</span>
                      </TableCell>
                      <TableCell className="text-[11px] text-muted-foreground">
                        {fact.observed_at ? fact.observed_at.slice(0, 10) : "—"}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{Math.round(fact.confidence * 100)}%</TableCell>
                      <TableCell className="text-right">
                        {fact.evidence_ids.length > 0 ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 gap-1 text-primary text-xs hover:text-primary"
                            onClick={() => openEvidenceForFact(fact.evidence_ids)}
                          >
                            <Eye className="size-3" />
                            <span>View ({fact.evidence_ids.length})</span>
                          </Button>
                        ) : (
                          <span className="text-[11px] text-muted-foreground">—</span>
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

      {/* Evidence View */}
      {activeTab === "evidence" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-base">Captured Evidence & Artifacts</h3>
            <span className="text-muted-foreground text-xs">{filteredEvidence.length} items</span>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filteredEvidence.length === 0 ? (
            <div className="col-span-full flex h-32 items-center justify-center rounded-lg border text-muted-foreground text-xs">
              No evidence artifacts found matching search query.
            </div>
          ) : (
            filteredEvidence.map((ev) => (
              <Card key={ev.id} className="overflow-hidden transition-colors hover:border-primary/50">
                <button
                  type="button"
                  className="group relative flex aspect-video w-full cursor-pointer items-center justify-center border-b bg-muted/60 text-left"
                  onClick={() => setSelectedEvidence(ev)}
                >
                  <div className="flex flex-col items-center gap-1.5 text-muted-foreground transition-colors group-hover:text-primary">
                    <ImageIcon className="size-8" />
                    <span className="font-medium text-[11px]">Click to inspect capture</span>
                  </div>
                  <Badge variant="secondary" className="absolute top-2 right-2 gap-1 text-[10px]">
                    <Shield className="size-2.5 text-emerald-600" />
                    <span>Robots: {ev.robots_decision}</span>
                  </Badge>
                </button>
                <CardHeader className="p-3 pb-1">
                  <div className="flex items-center justify-between">
                    <span className="max-w-[200px] truncate font-medium text-xs">{ev.url ?? ev.path}</span>
                    <Badge variant="outline" className="text-[10px]">
                      {ev.facts_count} facts
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 p-3 pt-1">
                  <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                    <span>Captured:</span>
                    <span>{fmtAbsolute(ev.captured_at)}</span>
                  </div>
                  <div className="flex items-center justify-between gap-1 rounded bg-muted/50 px-2 py-1 font-mono text-[11px]">
                    <span className="truncate">SHA256: {ev.sha256.slice(0, 16)}...</span>
                    <button
                      type="button"
                      onClick={() => copyText(ev.sha256, "SHA256 Hash")}
                      className="text-muted-foreground hover:text-foreground"
                      title="Copy full hash"
                      aria-label="Copy full SHA256 hash"
                    >
                      <Copy className="size-3" />
                    </button>
                  </div>
                </CardContent>
              </Card>
            ))
          )}
          </div>
        </div>
      )}

      {/* Evidence Lightbox Dialog */}
      <Dialog open={selectedEvidence !== null} onOpenChange={(open) => !open && setSelectedEvidence(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center justify-between text-base">
              <span>Evidence Artifact</span>
              {selectedEvidence?.robots_decision && (
                <Badge variant="outline" className="gap-1 text-xs">
                  <Shield className="size-3 text-emerald-600" />
                  <span>Robots: {selectedEvidence.robots_decision}</span>
                </Badge>
              )}
            </DialogTitle>
            <DialogDescription className="text-xs">
              Raw evidence screenshot and capture metadata captured by Farm crawlers.
            </DialogDescription>
          </DialogHeader>

          {selectedEvidence && (
            <div className="space-y-4 pt-2">
              <div className="relative flex aspect-video w-full items-center justify-center overflow-hidden rounded-lg border bg-muted/80">
                <div className="flex flex-col items-center gap-2 text-muted-foreground">
                  <ImageIcon className="size-12" />
                  <span className="font-mono text-xs">{selectedEvidence.path}</span>
                </div>
              </div>

              <div className="space-y-2 rounded-lg border p-3 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Source URL:</span>
                  {selectedEvidence.url ? (
                    <a
                      href={selectedEvidence.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 font-mono text-primary hover:underline"
                    >
                      <span>{selectedEvidence.url}</span>
                      <ExternalLink className="size-3" />
                    </a>
                  ) : (
                    <span>—</span>
                  )}
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Tool Version:</span>
                  <span className="font-mono">{selectedEvidence.tool_version ?? "N/A"}</span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Captured At:</span>
                  <span>{fmtAbsolute(selectedEvidence.captured_at)}</span>
                </div>

                <div className="flex items-center justify-between gap-2 border-t pt-1">
                  <span className="shrink-0 text-muted-foreground">SHA256:</span>
                  <span className="truncate font-mono text-[11px]">{selectedEvidence.sha256}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() => copyText(selectedEvidence.sha256, "SHA256 Hash")}
                  >
                    Copy
                  </Button>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
