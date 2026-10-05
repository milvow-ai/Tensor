"use client";

import { useMemo, useState } from "react";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import { SectionTitle } from "@/components/farm/section-title";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtInt, fmtUsd } from "@/lib/farm/format";
import type { CostPerResultRow } from "@/lib/farm/types";

type SortField = "cost_per_result" | "spend_usd" | "successful_results" | "provider_name";

function SortHeader({
  field,
  label,
  className,
  currentField,
  sortAsc,
  onToggle,
}: {
  field: SortField;
  label: string;
  className?: string;
  currentField: SortField;
  sortAsc: boolean;
  onToggle: (field: SortField) => void;
}) {
  const active = currentField === field;
  let icon = <ArrowUpDown className="ml-1 size-3.5 opacity-40" />;
  if (active) {
    icon = sortAsc ? <ArrowUp className="ml-1 size-3.5" /> : <ArrowDown className="ml-1 size-3.5" />;
  }

  return (
    <TableHead className={className}>
      <Button
        variant="ghost"
        size="sm"
        className="-ml-3 h-8 font-medium text-xs hover:bg-transparent"
        onClick={() => onToggle(field)}
      >
        <span>{label}</span>
        {icon}
      </Button>
    </TableHead>
  );
}

export function CostPerResultTable({ rows }: { rows: CostPerResultRow[] }) {
  const [sortField, setSortField] = useState<SortField>("cost_per_result");
  const [sortAsc, setSortAsc] = useState<boolean>(false);

  function toggleSort(field: SortField) {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(false); // Default descending for numbers
    }
  }

  const sortedRows = useMemo(() => {
    return [...rows].sort((a, b) => {
      let cmp = 0;
      if (sortField === "provider_name") {
        cmp = a.provider_name.localeCompare(b.provider_name);
      } else if (sortField === "cost_per_result") {
        const valA = a.cost_per_result ?? -1;
        const valB = b.cost_per_result ?? -1;
        cmp = valA - valB;
      } else if (sortField === "spend_usd") {
        cmp = a.spend_usd - b.spend_usd;
      } else if (sortField === "successful_results") {
        cmp = a.successful_results - b.successful_results;
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [rows, sortField, sortAsc]);

  return (
    <Card>
      <CardHeader>
        <SectionTitle>Cost per result</SectionTitle>
        <CardDescription>
          Spend divided by successful results completed this month across providers and accounts.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <SortHeader
                field="provider_name"
                label="Provider & Account"
                currentField={sortField}
                sortAsc={sortAsc}
                onToggle={toggleSort}
              />
              <SortHeader
                field="spend_usd"
                label="Spend MTD"
                className="text-right"
                currentField={sortField}
                sortAsc={sortAsc}
                onToggle={toggleSort}
              />
              <SortHeader
                field="successful_results"
                label="Successful Calls"
                className="text-right"
                currentField={sortField}
                sortAsc={sortAsc}
                onToggle={toggleSort}
              />
              <SortHeader
                field="cost_per_result"
                label="Cost / Result"
                className="text-right"
                currentField={sortField}
                sortAsc={sortAsc}
                onToggle={toggleSort}
              />
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedRows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="h-24 text-center text-muted-foreground text-sm">
                  No execution results recorded this month.
                </TableCell>
              </TableRow>
            ) : (
              sortedRows.map((r) => (
                <TableRow key={r.connection_id} data-testid={`cpr-row-${r.connection_id}`}>
                  <TableCell>
                    <div className="flex flex-col">
                      <span className="font-medium text-sm">{r.provider_name}</span>
                      <span className="font-mono text-muted-foreground text-xs">{r.connection_label}</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">{fmtUsd(r.spend_usd)}</TableCell>
                  <TableCell className="text-right font-mono text-sm tabular-nums">
                    {fmtInt(r.successful_results)}
                  </TableCell>
                  <TableCell className="text-right">
                    {r.cost_per_result !== null ? (
                      <span className="font-mono font-semibold text-foreground text-sm tabular-nums">
                        ${r.cost_per_result.toFixed(4)}
                        <span className="font-normal text-muted-foreground text-xs">/res</span>
                      </span>
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
  );
}
