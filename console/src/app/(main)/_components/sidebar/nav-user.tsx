"use client";

import { ChevronsUpDown } from "lucide-react";

import { useLazy } from "@/components/farm/use-lazy";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import { getInitials } from "@/lib/utils";

export interface NavUserProps {
  readonly user: { readonly name: string; readonly email: string; readonly mode: "supabase" | "fixtures" };
}

const loadMenu = () => import("./nav-user-menu").then((module) => module.NavUserMenu);

/** Account block at the foot of the sidebar. The menu behind it (sign out) loads right after first paint. */
export function NavUser({ user }: NavUserProps) {
  const Menu = useLazy(loadMenu);
  if (Menu) return <Menu user={user} />;
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton size="lg" aria-busy="true">
          <Avatar className="size-8 rounded-lg">
            <AvatarFallback className="rounded-lg bg-sidebar-accent text-sidebar-accent-foreground text-xs">
              {getInitials(user.name)}
            </AvatarFallback>
          </Avatar>
          <div className="grid flex-1 text-left text-sm leading-tight">
            <span className="truncate font-medium">{user.name}</span>
            <span className="truncate text-sidebar-foreground/60 text-xs">{user.email}</span>
          </div>
          <ChevronsUpDown aria-hidden="true" className="ml-auto size-4 text-sidebar-foreground/60" />
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
