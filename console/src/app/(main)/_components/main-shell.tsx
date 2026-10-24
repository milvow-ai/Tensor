"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import {
  Activity,
  Brain,
  CheckCircle2,
  Cpu,
  LayoutDashboard,
  PanelLeft,
  Settings,
  ShieldCheck,
  Webhook,
  Wrench,
  X,
  Zap,
} from "lucide-react";

import { FarmMark } from "@/components/farm/brand";
import { AppSidebar, type AppSidebarSection } from "@/components/kit";

import { DataSourceBadge } from "./header/data-source-badge";
import { ThemeSwitcher } from "./header/theme-switcher";
import { UserCard } from "./sidebar/user-card";
import { LiveDot, TopBarNotifications, TopBarPeriodSelect, TopBarSearch } from "./top-bar";

export interface MainShellProps {
  user: {
    readonly name: string;
    readonly email: string;
    readonly mode: "supabase" | "fixtures";
  };
  defaultCollapsed?: boolean;
  openAlertsCount?: number;
  children: React.ReactNode;
}

const SIDEBAR_SECTIONS: AppSidebarSection[] = [
  {
    id: "main",
    label: "MAIN",
    items: [
      { title: "Overview", href: "/overview", icon: LayoutDashboard },
      { title: "Activity", href: "/runs", icon: Activity },
      { title: "Tasks", href: "/tasks", icon: CheckCircle2 },
    ],
  },
  {
    id: "farm",
    label: "FARM",
    items: [
      { title: "AI", href: "/pools/ai", icon: Cpu },
      { title: "Tools", href: "/pools/tools", icon: Wrench },
      { title: "APIs", href: "/integrations", icon: Webhook },
    ],
  },
  {
    id: "manage",
    items: [
      { title: "Usage & Cost", href: "/usage", icon: Zap },
      { title: "Memory", href: "/memory", icon: Brain },
      { title: "Approvals", href: "/approvals", icon: ShieldCheck },
    ],
  },
  {
    id: "others",
    label: "OTHERS",
    items: [{ title: "Settings", href: "/settings", icon: Settings }],
  },
];

function resolveActiveHref(pathname: string): string {
  if (pathname === "/overview") return "/overview";
  if (pathname.startsWith("/runs")) return "/runs";
  if (pathname.startsWith("/tasks")) return "/tasks";
  if (pathname.startsWith("/pools/ai")) return "/pools/ai";
  if (pathname.startsWith("/pools/tools") || pathname.startsWith("/pools/")) return "/pools/tools";
  if (pathname.startsWith("/integrations")) return "/integrations";
  if (pathname.startsWith("/usage") || pathname.startsWith("/billing")) return "/usage";
  if (pathname.startsWith("/memory")) return "/memory";
  if (pathname.startsWith("/approvals")) return "/approvals";
  if (pathname.startsWith("/settings") || pathname.startsWith("/policies") || pathname.startsWith("/routing")) {
    return "/settings";
  }
  return pathname;
}

export function MainShell({
  user,
  defaultCollapsed = false,
  openAlertsCount = 0,
  children,
}: MainShellProps) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Close mobile drawer on route navigation
  useEffect(() => {
    if (pathname) {
      setMobileOpen(false);
    }
  }, [pathname]);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      document.cookie = `sidebar_collapsed=${next}; path=/; max-age=31536000; SameSite=Lax`;
      return next;
    });
  };

  const activeHref = resolveActiveHref(pathname);

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[var(--kit-bg-canvas,#f4f5f7)] text-[var(--kit-text-primary,#111827)] dark:bg-[#0d1117] dark:text-[#f0f6fc]">
      {/* Desktop sidebar */}
      <div className="hidden h-full shrink-0 md:flex">
        <AppSidebar
          logo={<FarmMark className="size-7 rounded-[var(--kit-radius-control,8px)]" />}
          brandName="Harness Farm"
          sections={SIDEBAR_SECTIONS}
          activeHref={activeHref}
          collapsed={collapsed}
          onToggleCollapse={toggleCollapsed}
          footer={<UserCard user={user} collapsed={collapsed} />}
        />
      </div>

      {/* Mobile drawer backdrop and overlay */}
      {mobileOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Navigation menu"
          className="fixed inset-0 z-50 flex md:hidden"
        >
          <button
            type="button"
            className="fixed inset-0 bg-black/50 backdrop-blur-xs transition-opacity"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation backdrop"
          />
          <div className="relative z-50 flex h-full w-64 flex-col bg-[var(--kit-bg-card,#ffffff)] shadow-xl dark:bg-[#161b22]">
            <div className="absolute top-3 right-2.5 z-10">
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                aria-label="Close navigation"
                className="cursor-pointer rounded p-1 text-[var(--kit-text-secondary,#5b6370)] hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:text-[#8b949e] dark:hover:bg-[#21262d]"
              >
                <X className="size-4" />
              </button>
            </div>
            <AppSidebar
              logo={<FarmMark className="size-7 rounded-[var(--kit-radius-control,8px)]" />}
              brandName="Harness Farm"
              sections={SIDEBAR_SECTIONS}
              activeHref={activeHref}
              collapsed={false}
              footer={<UserCard user={user} collapsed={false} />}
            />
          </div>
        </div>
      )}

      {/* Main content pane */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 shrink-0 items-center justify-between border-[var(--kit-border,#e6e8ec)] border-b bg-[var(--kit-bg-card,#ffffff)] px-3 sm:px-4 md:px-6 dark:border-[#30363d] dark:bg-[#161b22]">
          <div className="flex min-w-0 items-center gap-2 sm:gap-3">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation menu"
              className="cursor-pointer rounded p-1.5 text-[var(--kit-text-secondary,#5b6370)] hover:bg-[var(--kit-bg-subtle,#f3f4f6)] md:hidden dark:text-[#8b949e] dark:hover:bg-[#21262d]"
            >
              <PanelLeft className="size-5" />
            </button>
            <TopBarSearch />
          </div>

          <div className="flex shrink-0 items-center gap-2 sm:gap-3">
            <TopBarPeriodSelect />
            <LiveDot />
            <TopBarNotifications count={openAlertsCount} />
            <div className="hidden items-center gap-2 sm:flex">
              <DataSourceBadge mode={user.mode} />
              <ThemeSwitcher />
            </div>
          </div>
        </header>

        {/* Page content */}
        <main tabIndex={0} aria-label="Page content" className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto p-4 md:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
