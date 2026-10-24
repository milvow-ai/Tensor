import type React from "react";
import { forwardRef } from "react";

import { FileText, MoreHorizontal } from "lucide-react";

import { formatCurrency } from "@/lib/format";

import { type FarmStatus, StatusChip } from "./StatusChip";

export interface TaskAgent {
  name: string;
  avatar?: string;
  role?: string;
}

export interface TaskFile {
  name: string;
  type?: string;
  thumbnail?: string;
}

export interface TaskCardProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string;
  operator: string;
  agents?: TaskAgent[];
  status: FarmStatus;
  cost?: number | string;
  files?: TaskFile[];
  time?: string;
  menu?: React.ReactNode;
  onMenuClick?: () => void;
  loading?: boolean;
}

export const TaskCard = forwardRef<HTMLDivElement, TaskCardProps>(
  (
    {
      title,
      operator,
      agents = [],
      status,
      cost,
      files = [],
      time,
      menu,
      onMenuClick,
      loading = false,
      className = "",
      ...props
    },
    ref,
  ) => {
    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-4 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="flex items-start justify-between">
            <div className="h-4 w-40 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-5 w-16 rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="h-3 w-28 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="flex items-center justify-between pt-2">
            <div className="flex -space-x-1.5">
              {[1, 2].map((i) => (
                <div
                  key={i}
                  className="h-6 w-6 rounded-full bg-[var(--kit-bg-subtle,#f3f4f6)] ring-2 ring-[var(--kit-bg-card,#ffffff)] dark:bg-[#21262d] dark:ring-[#161b22]"
                />
              ))}
            </div>
            <div className="h-3 w-12 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
        </div>
      );
    }

    const formattedCost = typeof cost === "number" ? formatCurrency(cost) : cost;

    const visibleFiles = files.slice(0, 3);
    const overflowFilesCount = files.length - 3;

    return (
      <div
        ref={ref}
        className={`space-y-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-4 shadow-[var(--kit-shadow-card)] transition-all hover:border-[var(--kit-accent,#1a63d6)]/40 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {/* Header: Title + Status + Menu */}
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 space-y-1">
            <h4 className="truncate font-semibold text-[var(--kit-text-primary,#111827)] text-sm leading-snug dark:text-[#f0f6fc]">
              {title}
            </h4>
            <p className="truncate text-[var(--kit-text-secondary,#5b6370)] text-xs dark:text-[#8b949e]">
              Operator:{" "}
              <span className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">{operator}</span>
              {time && ` · ${time}`}
            </p>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            <StatusChip status={status} size="sm" />
            {menu}
            {!menu && onMenuClick && (
              <button
                type="button"
                onClick={onMenuClick}
                aria-label="Task options"
                className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-[var(--kit-radius-control,8px)] text-[var(--kit-text-secondary,#5b6370)] transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] hover:text-[var(--kit-text-primary,#111827)] dark:text-[#8b949e] dark:hover:bg-[#21262d] dark:hover:text-[#f0f6fc]"
              >
                <MoreHorizontal className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Files thumbnails / pills */}
        {files.length > 0 && (
          <div className="flex items-center gap-1.5 overflow-hidden">
            {visibleFiles.map((file) => (
              <div
                key={file.name}
                className="inline-flex max-w-[120px] items-center gap-1 truncate rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-bg-subtle,#f3f4f6)] px-2 py-0.5 text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:bg-[#21262d] dark:text-[#8b949e]"
              >
                <FileText className="h-3 w-3 shrink-0" />
                <span className="truncate">{file.name}</span>
              </div>
            ))}
            {overflowFilesCount > 0 && (
              <span className="px-1 font-medium text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
                +{overflowFilesCount}
              </span>
            )}
          </div>
        )}

        {/* Footer: Agents Avatars + Cost */}
        <div className="flex items-center justify-between border-[var(--kit-border,#e6e8ec)] border-t pt-2 text-xs dark:border-[#30363d]">
          <div className="flex items-center gap-1.5">
            {agents.length > 0 ? (
              <div className="flex -space-x-1.5 overflow-hidden">
                {agents.map((agent) => (
                  <div
                    key={agent.name}
                    title={`${agent.name}${agent.role ? ` (${agent.role})` : ""}`}
                    className="inline-flex h-6 w-6 select-none items-center justify-center rounded-full bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] font-bold text-[10px] text-[var(--kit-accent,#1a63d6)] ring-2 ring-[var(--kit-bg-card,#ffffff)] dark:text-[#58a6ff] dark:ring-[#161b22]"
                  >
                    {agent.name.charAt(0).toUpperCase()}
                  </div>
                ))}
              </div>
            ) : (
              <span className="text-[11px] text-[var(--kit-text-secondary,#5b6370)] dark:text-[#8b949e]">
                Autonomous
              </span>
            )}
          </div>

          {formattedCost && (
            <div className="font-semibold text-[var(--kit-text-primary,#111827)] text-xs tabular-nums dark:text-[#f0f6fc]">
              {formattedCost}
            </div>
          )}
        </div>
      </div>
    );
  },
);

TaskCard.displayName = "TaskCard";

export interface TaskGroup {
  id: string;
  title: string;
  tasks: TaskCardProps[];
}

export interface TaskBoardProps extends React.HTMLAttributes<HTMLDivElement> {
  groups: TaskGroup[];
  loading?: boolean;
  empty?: boolean;
  emptyMessage?: string;
}

export const TaskBoard = forwardRef<HTMLDivElement, TaskBoardProps>(
  (
    {
      groups,
      loading = false,
      empty = false,
      emptyMessage = "No tasks found for this schedule.",
      className = "",
      ...props
    },
    ref,
  ) => {
    if (loading) {
      return (
        <div ref={ref} className={`grid grid-cols-1 gap-4 md:grid-cols-3 ${className}`} {...props}>
          {[1, 2, 3].map((g) => (
            <div key={g} className="space-y-3">
              <div className="h-4 w-28 animate-pulse rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
              <TaskCard title="..." operator="..." status="ready" loading />
              <TaskCard title="..." operator="..." status="ready" loading />
            </div>
          ))}
        </div>
      );
    }

    if (empty || groups.length === 0) {
      return (
        <div
          ref={ref}
          className={`rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-10 text-center text-[var(--kit-text-secondary,#5b6370)] text-xs dark:border-[#30363d] dark:bg-[#161b22] dark:text-[#8b949e] ${className}`}
          {...props}
        >
          {emptyMessage}
        </div>
      );
    }

    return (
      <div ref={ref} className={`grid grid-cols-1 gap-4 md:grid-cols-3 ${className}`} {...props}>
        {groups.map((group) => (
          <div key={group.id} className="space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="font-bold text-[var(--kit-text-secondary,#5b6370)] text-xs uppercase tracking-wider dark:text-[#8b949e]">
                {group.title}
              </h4>
              <span className="rounded bg-[var(--kit-bg-subtle,#f3f4f6)] px-1.5 py-0.5 font-semibold text-[11px] text-[var(--kit-text-secondary,#5b6370)] tabular-nums dark:bg-[#21262d] dark:text-[#8b949e]">
                {group.tasks.length}
              </span>
            </div>

            <div className="space-y-3">
              {group.tasks.map((task) => (
                <TaskCard key={task.title} {...task} />
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  },
);

TaskBoard.displayName = "TaskBoard";
