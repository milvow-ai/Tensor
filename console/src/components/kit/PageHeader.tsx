import React, { forwardRef } from "react";

export interface PageHeaderProps extends React.HTMLAttributes<HTMLElement> {
  title: string;
  subtitle?: string;
  search?: React.ReactNode;
  bell?: React.ReactNode;
  periodSelect?: React.ReactNode;
  actions?: React.ReactNode[];
  userMenu?: React.ReactNode;
  loading?: boolean;
}

export const PageHeader = forwardRef<HTMLElement, PageHeaderProps>(
  (
    { title, subtitle, search, bell, periodSelect, actions = [], userMenu, loading = false, className = "", ...props },
    ref,
  ) => {
    if (loading) {
      return (
        <header
          ref={ref}
          className={`flex animate-pulse flex-col justify-between gap-4 border-[var(--kit-border,#e6e8ec)] border-b bg-[var(--kit-bg-card,#ffffff)] px-6 py-4 md:flex-row md:items-center dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="space-y-1.5">
            <div className="h-6 w-44 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-3.5 w-64 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
          <div className="flex items-center gap-3">
            <div className="h-9 w-48 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="h-9 w-32 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          </div>
        </header>
      );
    }

    return (
      <header
        ref={ref}
        className={`flex select-none flex-col justify-between gap-4 border-[var(--kit-border,#e6e8ec)] border-b bg-[var(--kit-bg-card,#ffffff)] px-6 py-4 lg:flex-row lg:items-center dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {/* Title and Subtitle */}
        <div className="min-w-0 space-y-0.5">
          <h1 className="truncate font-bold text-[var(--kit-text-primary,#111827)] text-lg tracking-tight sm:text-xl dark:text-[#f0f6fc]">
            {title}
          </h1>
          {subtitle && (
            <p className="truncate text-[var(--kit-text-secondary,#6b7280)] text-xs dark:text-[#8b949e]">{subtitle}</p>
          )}
        </div>

        {/* Controls, period selector, search, notifications, actions */}
        <div className="flex flex-wrap items-center gap-2.5 sm:gap-3">
          {search && <div>{search}</div>}
          {periodSelect && <div>{periodSelect}</div>}
          {bell && <div>{bell}</div>}

          {actions.length > 0 && (
            <div className="flex items-center gap-2">
              {actions.map((action) => {
                if (React.isValidElement(action) && action.key) {
                  return <React.Fragment key={action.key}>{action}</React.Fragment>;
                }
                return action;
              })}
            </div>
          )}

          {userMenu && (
            <div className="border-[var(--kit-border,#e6e8ec)] border-l pl-1 dark:border-[#30363d]">{userMenu}</div>
          )}
        </div>
      </header>
    );
  },
);

PageHeader.displayName = "PageHeader";
