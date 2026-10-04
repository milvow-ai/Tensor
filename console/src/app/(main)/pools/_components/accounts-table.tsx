"use client";

import { createContext, useContext, useId, useTransition } from "react";

import { usePathname, useRouter } from "next/navigation";

import { type ColumnDef, type PaginationState, type SortingState, type Updater, useTable } from "@tanstack/react-table";
import { cn } from "cn";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Server,
} from "lucide-react";

import { CommandChip } from "@/components/farm/command-chip";
import { useSharedCommands } from "@/components/farm/commands-provider";
import { EmptyState } from "@/components/farm/states";
import type { TrackedCommand } from "@/components/farm/use-command";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type DataTableFeatures, dataTableFeatures } from "@/lib/data-table-features";
import { fmtInt } from "@/lib/farm/format";
import type { Connection, ConnectionSortKey, SortDirection } from "@/lib/farm/types";

import { type ListParams, PAGE_SIZES, toSearch } from "../_lib/list-params";
import {
  AccountCell,
  ActiveSwitch,
  CallsCell,
  CliCell,
  HealthCell,
  LimitCell,
  PlanCell,
  PriorityInput,
  ResetCell,
  RowActions,
  StatusCell,
  UsageCell,
} from "./account-cells";

type Variant = "tool" | "ai";

interface Controls {
  sort: ConnectionSortKey;
  dir: SortDirection;
  onSort: (key: ConnectionSortKey) => void;
}

const ControlsContext = createContext<Controls | null>(null);

function sortIcon(active: boolean, dir: SortDirection) {
  if (!active) return ArrowUpDown;
  return dir === "asc" ? ArrowUp : ArrowDown;
}

const SORT_VALUE: Record<ConnectionSortKey, (row: Connection) => string | number> = {
  label: (row) => row.label,
  status: (row) => row.status,
  priority: (row) => row.priority,
};

function ariaSort(sorted: false | "asc" | "desc"): "ascending" | "descending" | undefined {
  if (sorted === "asc") return "ascending";
  if (sorted === "desc") return "descending";
  return undefined;
}

function SortHeader({ id, label, align = "left" }: { id: ConnectionSortKey; label: string; align?: "left" | "right" }) {
  const controls = useContext(ControlsContext);
  if (!controls) return label;
  const active = controls.sort === id;
  const Icon = sortIcon(active, controls.dir);
  return (
    <button
      type="button"
      onClick={() => controls.onSort(id)}
      aria-label={`Sort by ${label.toLowerCase()}`}
      className={cn(
        "-mx-2 inline-flex h-7 items-center gap-1 rounded-md px-2 font-medium hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
        align === "right" ? "flex-row-reverse" : null,
        active ? "text-foreground" : null,
      )}
    >
      {label}
      <Icon aria-hidden="true" className={cn("size-3", active ? "opacity-100" : "opacity-40")} />
    </button>
  );
}

type Column = ColumnDef<DataTableFeatures, Connection>;

const sortable = (id: ConnectionSortKey, label: string, align: "left" | "right" = "left") => ({
  id,
  accessorFn: SORT_VALUE[id],
  header: () => <SortHeader id={id} label={label} align={align} />,
});

const priorityColumn: Column = {
  ...sortable("priority", "Priority", "right"),
  cell: ({ row }) => <PriorityInput key={row.original.priority} connection={row.original} />,
  meta: { align: "right" },
};
const enabledColumn: Column = {
  id: "enabled",
  header: "Enabled",
  cell: ({ row }) => <ActiveSwitch connection={row.original} />,
};
const actionsColumn: Column = {
  id: "actions",
  header: () => <span className="sr-only">Actions</span>,
  cell: ({ row }) => <RowActions connection={row.original} />,
};

const TOOL_COLUMNS: Column[] = [
  { ...sortable("label", "Account"), cell: ({ row }) => <AccountCell connection={row.original} /> },
  { ...sortable("status", "Status"), cell: ({ row }) => <StatusCell connection={row.original} /> },
  { id: "usage", header: "Usage and next reset", cell: ({ row }) => <UsageCell connection={row.original} showReset /> },
  { id: "plan", header: "Plan", cell: ({ row }) => <PlanCell connection={row.original} /> },
  { id: "health", header: "Health", cell: ({ row }) => <HealthCell connection={row.original} /> },
  priorityColumn,
  enabledColumn,
  actionsColumn,
];

const AI_COLUMNS: Column[] = [
  { ...sortable("label", "Account"), cell: ({ row }) => <AccountCell connection={row.original} /> },
  { ...sortable("status", "Login and status"), cell: ({ row }) => <StatusCell connection={row.original} ai /> },
  { id: "cli", header: "CLI and models", cell: ({ row }) => <CliCell connection={row.original} /> },
  { id: "limits", header: "Limits", cell: ({ row }) => <LimitCell connection={row.original} /> },
  { id: "health", header: "Health", cell: ({ row }) => <HealthCell connection={row.original} /> },
  {
    id: "calls",
    header: () => <span className="block text-right">Today</span>,
    cell: ({ row }) => <CallsCell connection={row.original} />,
  },
  priorityColumn,
  enabledColumn,
  actionsColumn,
];

function applyUpdater<T>(updater: Updater<T>, previous: T): T {
  return typeof updater === "function" ? (updater as (old: T) => T)(previous) : updater;
}

/** An account the owner just asked to add: a placeholder row until the Farm creates it. */
function AddingRow({ id, command, columns }: { id: string; command: TrackedCommand; columns: number }) {
  return (
    <TableRow data-testid="adding-row" className="bg-muted/30">
      <TableCell colSpan={columns}>
        <div className="flex items-center gap-3">
          <div className="min-w-0">
            <div className="font-medium text-[13px]">{command.subject ?? id}</div>
            <div className="font-mono text-muted-foreground text-xs">{id}</div>
          </div>
          <CommandChip command={command} />
          <span className="text-muted-foreground text-xs">Waiting for the Farm to add this account.</span>
        </div>
      </TableCell>
    </TableRow>
  );
}

function MobileCard({ connection, variant }: { connection: Connection; variant: Variant }) {
  const ai = variant === "ai";
  return (
    <li className="rounded-xl border bg-card p-3.5" data-testid="account-card">
      <div className="flex items-start justify-between gap-2">
        <AccountCell connection={connection} />
        <RowActions connection={connection} />
      </div>
      <div className="mt-3">
        <StatusCell connection={connection} ai={ai} />
      </div>
      <div className="mt-3 grid gap-3">
        {ai ? <LimitCell connection={connection} /> : <UsageCell connection={connection} />}
      </div>
      {ai ? (
        <div className="mt-3">
          <CliCell connection={connection} />
        </div>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-3">
          <PlanCell connection={connection} />
          <ResetCell connection={connection} />
        </div>
      )}
      <div className="mt-3">
        <HealthCell connection={connection} />
      </div>
      <div className="mt-3 flex items-center justify-between gap-3 border-t pt-3">
        <div className="flex items-center gap-2 text-xs">
          <ActiveSwitch connection={connection} />
          <span className="text-muted-foreground">Enabled</span>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Priority</span>
          <PriorityInput key={connection.priority} connection={connection} />
        </div>
      </div>
    </li>
  );
}

export function AccountsTable({
  items,
  total,
  params,
  variant,
}: {
  items: Connection[];
  total: number;
  params: ListParams;
  variant: Variant;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [isPending, startTransition] = useTransition();
  const { pending } = useSharedCommands();
  const pageSizeId = useId();

  const navigate = (next: ListParams) => {
    startTransition(() => router.replace(`${pathname}${toSearch(next)}`, { scroll: false }));
  };

  const sorting: SortingState = [{ id: params.sort, desc: params.dir === "desc" }];
  const pagination: PaginationState = { pageIndex: params.page - 1, pageSize: params.pageSize };
  const columns = variant === "ai" ? AI_COLUMNS : TOOL_COLUMNS;

  const table = useTable({
    features: dataTableFeatures,
    data: items,
    columns,
    state: { sorting, pagination },
    manualSorting: true,
    manualPagination: true,
    rowCount: total,
    getRowId: (row) => row.id,
    onPaginationChange: (updater) => {
      const next = applyUpdater(updater, pagination);
      navigate({
        ...params,
        page: next.pageSize === params.pageSize ? next.pageIndex + 1 : 1,
        pageSize: next.pageSize,
      });
    },
  });

  const onSort = (key: ConnectionSortKey) => {
    if (params.sort !== key) navigate({ ...params, page: 1, sort: key, dir: "asc" });
    else if (params.dir === "asc") navigate({ ...params, page: 1, sort: key, dir: "desc" });
    else navigate({ ...params, page: 1, sort: "priority", dir: "asc" });
  };

  const pageCount = Math.max(table.getPageCount(), 1);
  const first = total === 0 ? 0 : (params.page - 1) * params.pageSize + 1;
  const last = Math.min(params.page * params.pageSize, total);
  const adding =
    params.page === 1
      ? Object.entries(pending)
          .filter(([key]) => key.startsWith("add:") && !items.some((item) => item.id === key.slice(4)))
          .map(([key, command]) => ({ id: key.slice(4), command }))
      : [];

  if (total === 0 && adding.length === 0) {
    return (
      <EmptyState
        icon={Server}
        title="No accounts in this pool yet"
        description="Add the first account; the Farm validates it and starts routing once it is active."
      />
    );
  }

  return (
    <ControlsContext.Provider value={{ sort: params.sort, dir: params.dir, onSort }}>
      <Card className="gap-0 py-0" aria-busy={isPending} data-testid="accounts-table">
        <div className={cn("transition-opacity", isPending ? "opacity-60" : null)}>
          <div className="hidden md:block">
            <Table>
              <TableHeader className="bg-muted/40">
                {table.getHeaderGroups().map((headerGroup) => (
                  <TableRow key={headerGroup.id} className="hover:bg-transparent">
                    {headerGroup.headers.map((header) => {
                      const sorted = header.column.getIsSorted();
                      const right = (header.column.columnDef.meta as { align?: string } | undefined)?.align === "right";
                      return (
                        <TableHead
                          key={header.id}
                          colSpan={header.colSpan}
                          aria-sort={ariaSort(sorted)}
                          className={cn("h-9 text-xs", right ? "text-right" : null)}
                        >
                          {header.isPlaceholder ? null : <table.FlexRender header={header} />}
                        </TableHead>
                      );
                    })}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {adding.map(({ id, command }) => (
                  <AddingRow key={id} id={id} command={command} columns={columns.length} />
                ))}
                {table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    data-testid="account-row"
                    data-account-id={row.original.id}
                    className="align-top"
                  >
                    {row.getAllCells().map((cell) => {
                      const right = (cell.column.columnDef.meta as { align?: string } | undefined)?.align === "right";
                      return (
                        <TableCell key={cell.id} className={cn("py-3", right ? "text-right" : null)}>
                          <table.FlexRender cell={cell} />
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <ul className="flex flex-col gap-3 p-3 md:hidden">
            {adding.map(({ id, command }) => (
              <li
                key={id}
                className="flex items-center gap-2 rounded-xl border border-dashed p-3.5"
                data-testid="adding-card"
              >
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-[13px]">{command.subject ?? id}</div>
                  <div className="font-mono text-muted-foreground text-xs">{id}</div>
                </div>
                <CommandChip command={command} />
              </li>
            ))}
            {items.map((connection) => (
              <MobileCard key={connection.id} connection={connection} variant={variant} />
            ))}
          </ul>
        </div>

        <div className="flex flex-col gap-3 border-t px-4 py-3 text-xs sm:flex-row sm:items-center sm:justify-between">
          <p className="text-muted-foreground tabular-nums" aria-live="polite">
            {total === 0 ? "No accounts" : `Showing ${fmtInt(first)} to ${fmtInt(last)} of ${fmtInt(total)} accounts`}
          </p>
          <div className="flex items-center justify-between gap-4 sm:justify-end">
            <div className="flex items-center gap-2 text-muted-foreground">
              <label htmlFor={`${pageSizeId}`}>Rows</label>
              <NativeSelect
                id={pageSizeId}
                size="sm"
                aria-label="Rows per page"
                value={params.pageSize}
                onChange={(event) => table.setPageSize(Number(event.target.value))}
              >
                {PAGE_SIZES.map((size) => (
                  <NativeSelectOption key={size} value={size}>
                    {size}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
            </div>
            <span className="font-medium tabular-nums">
              Page {params.page} of {pageCount}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="outline"
                size="icon"
                className="hidden size-7 sm:inline-flex"
                disabled={params.page <= 1}
                onClick={() => table.setPageIndex(0)}
                aria-label="First page"
              >
                <ChevronsLeft aria-hidden="true" />
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="size-7"
                disabled={params.page <= 1}
                onClick={() => table.previousPage()}
                aria-label="Previous page"
              >
                <ChevronLeft aria-hidden="true" />
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="size-7"
                disabled={params.page >= pageCount}
                onClick={() => table.nextPage()}
                aria-label="Next page"
              >
                <ChevronRight aria-hidden="true" />
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="hidden size-7 sm:inline-flex"
                disabled={params.page >= pageCount}
                onClick={() => table.setPageIndex(pageCount - 1)}
                aria-label="Last page"
              >
                <ChevronsRight aria-hidden="true" />
              </Button>
            </div>
          </div>
        </div>
      </Card>
    </ControlsContext.Provider>
  );
}
