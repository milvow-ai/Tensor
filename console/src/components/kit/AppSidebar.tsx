import type React from "react";
import { forwardRef } from "react";

import { ChevronLeft, ChevronRight } from "lucide-react";

export interface NavItemData {
  title: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: number | string;
  badgeTone?: "accent" | "warning" | "neutral";
}

export interface NavItemProps extends React.AnchorHTMLAttributes<HTMLAnchorElement> {
  item: NavItemData;
  isActive: boolean;
  collapsed?: boolean;
}

export const NavItem = forwardRef<HTMLAnchorElement, NavItemProps>(
  ({ item, isActive, collapsed = false, className = "", ...props }, ref) => {
    const IconComponent = item.icon;

    const activeClasses = isActive
      ? "bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] text-[var(--kit-accent,#1f6feb)] dark:text-[#58a6ff] font-semibold"
      : "text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e] hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:hover:bg-[#21262d] hover:text-[var(--kit-text-primary,#111827)] dark:hover:text-[#f0f6fc]";

    return (
      <a
        ref={ref}
        href={item.href}
        title={collapsed ? item.title : undefined}
        aria-current={isActive ? "page" : undefined}
        className={`flex cursor-pointer select-none items-center gap-2.5 rounded-[var(--kit-radius-control,8px)] px-3 py-2 text-xs transition-colors ${activeClasses} ${
          collapsed ? "justify-center px-2" : ""
        } ${className}`}
        {...props}
      >
        <IconComponent
          className={`h-4 w-4 shrink-0 ${
            isActive
              ? "text-[var(--kit-accent,#1f6feb)] dark:text-[#58a6ff]"
              : "text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]"
          }`}
        />
        {!collapsed && <span className="flex-1 truncate">{item.title}</span>}
        {!collapsed && item.badge !== undefined && (
          <span
            className={`shrink-0 rounded-full px-1.5 py-0.5 font-semibold text-[10px] tabular-nums ${
              isActive
                ? "bg-[var(--kit-accent,#1f6feb)] text-white"
                : "bg-[var(--kit-bg-subtle,#f3f4f6)] text-[var(--kit-text-secondary,#6b7280)] dark:bg-[#21262d] dark:text-[#8b949e]"
            }`}
          >
            {item.badge}
          </span>
        )}
      </a>
    );
  },
);

NavItem.displayName = "NavItem";

export interface NavSectionProps {
  label?: string;
  items: NavItemData[];
  activeHref: string;
  collapsed?: boolean;
}

export const NavSection: React.FC<NavSectionProps> = ({ label, items, activeHref, collapsed = false }) => {
  return (
    <div className="space-y-1">
      {label && !collapsed && (
        <div className="px-3 py-1.5 font-bold text-[10px] text-[var(--kit-text-muted,#9ca3af)] uppercase tracking-wider dark:text-[#6e7681]">
          {label}
        </div>
      )}
      <div className="space-y-0.5">
        {items.map((item) => (
          <NavItem key={item.href} item={item} isActive={activeHref === item.href} collapsed={collapsed} />
        ))}
      </div>
    </div>
  );
};

export interface AppSidebarSection {
  id: string;
  label?: string;
  items: NavItemData[];
}

export interface AppSidebarProps extends React.HTMLAttributes<HTMLElement> {
  logo?: React.ReactNode;
  brandName?: string;
  sections: AppSidebarSection[];
  activeHref: string;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  footer?: React.ReactNode;
  loading?: boolean;
}

export const AppSidebar = forwardRef<HTMLElement, AppSidebarProps>(
  (
    {
      logo,
      brandName = "Harness Farm",
      sections,
      activeHref,
      collapsed = false,
      onToggleCollapse,
      footer,
      loading = false,
      className = "",
      ...props
    },
    ref,
  ) => {
    if (loading) {
      return (
        <aside
          ref={ref}
          className={`flex h-full animate-pulse flex-col space-y-4 border-[var(--kit-border,#e6e8ec)] border-r bg-[var(--kit-bg-card,#ffffff)] p-3 dark:border-[#30363d] dark:bg-[#161b22] ${
            collapsed ? "w-16" : "w-64"
          } ${className}`}
          {...props}
        >
          <div className="h-9 w-full rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
          <div className="flex-1 space-y-2 pt-4">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="h-8 w-full rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            ))}
          </div>
        </aside>
      );
    }

    return (
      <aside
        ref={ref}
        aria-label="Application navigation"
        className={`flex h-full select-none flex-col border-[var(--kit-border,#e6e8ec)] border-r bg-[var(--kit-bg-card,#ffffff)] transition-all duration-200 dark:border-[#30363d] dark:bg-[#161b22] ${
          collapsed ? "w-16" : "w-64"
        } ${className}`}
        {...props}
      >
        {/* Brand / Logo Header */}
        <div className="flex shrink-0 items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b p-3.5 dark:border-[#30363d]">
          <div className="flex items-center gap-2.5 truncate">
            {logo ?? (
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-accent,#1f6feb)] font-bold text-sm text-white">
                HF
              </div>
            )}
            {!collapsed && (
              <span className="truncate font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
                {brandName}
              </span>
            )}
          </div>

          {onToggleCollapse && (
            <button
              type="button"
              onClick={onToggleCollapse}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="cursor-pointer rounded-[var(--kit-radius-control,8px)] p-1 text-[var(--kit-text-secondary,#6b7280)] transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:text-[#8b949e] dark:hover:bg-[#21262d]"
            >
              {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            </button>
          )}
        </div>

        {/* Navigation Sections */}
        <nav className="flex-1 min-h-0 space-y-4 overflow-y-auto p-3">
          {sections.map((section) => (
            <NavSection
              key={section.id}
              label={section.label}
              items={section.items}
              activeHref={activeHref}
              collapsed={collapsed}
            />
          ))}
        </nav>

        {/* Footer / PlanCard */}
        {footer && (
          <div className="shrink-0 border-[var(--kit-border,#e6e8ec)] border-t p-3 dark:border-[#30363d]">{footer}</div>
        )}
      </aside>
    );
  },
);

AppSidebar.displayName = "AppSidebar";
