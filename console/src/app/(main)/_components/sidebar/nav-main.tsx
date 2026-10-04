"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import type { NavGroup, NavMainItem } from "@/navigation/sidebar/sidebar-items";

/** The tool and AI pool pages share one nav entry each, so a detail page keeps its parent highlighted. */
function isActive(pathname: string, item: NavMainItem): boolean {
  if (item.id === "tools-pools") {
    return pathname.startsWith("/pools") && !pathname.startsWith("/pools/ai");
  }
  return pathname === item.url || pathname.startsWith(`${item.url}/`);
}

export function NavMain({ groups }: { readonly groups: readonly NavGroup[] }) {
  const pathname = usePathname();
  const { isMobile, setOpenMobile } = useSidebar();

  return (
    <>
      {groups.map((group) => (
        <SidebarGroup key={group.id} className="py-1.5">
          {group.label ? (
            <SidebarGroupLabel className="h-7 font-medium text-[11px] text-sidebar-foreground/55 uppercase tracking-wider">
              {group.label}
            </SidebarGroupLabel>
          ) : null}
          <SidebarGroupContent>
            <SidebarMenu className="gap-0.5">
              {group.items.map((item) => {
                const active = isActive(pathname, item);
                return (
                  <SidebarMenuItem key={item.id}>
                    <SidebarMenuButton
                      asChild
                      isActive={active}
                      tooltip={item.title}
                      className="h-8 text-[13px] data-[active=true]:bg-sidebar-accent data-[active=true]:font-medium data-[active=true]:text-sidebar-accent-foreground"
                    >
                      <Link
                        href={item.url}
                        prefetch={false}
                        aria-current={active ? "page" : undefined}
                        onClick={() => {
                          if (isMobile) setOpenMobile(false);
                        }}
                      >
                        {item.icon ? <item.icon aria-hidden="true" /> : null}
                        <span>{item.title}</span>
                      </Link>
                    </SidebarMenuButton>
                    {item.badge ? (
                      <SidebarMenuBadge className="mr-1 rounded-md border border-sidebar-border px-1.5 font-medium text-[10px] text-sidebar-foreground/60 tracking-wide">
                        {item.badge}
                      </SidebarMenuBadge>
                    ) : null}
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      ))}
    </>
  );
}
