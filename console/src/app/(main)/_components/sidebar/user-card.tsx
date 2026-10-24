"use client";

import { ChevronsUpDown, LogOut } from "lucide-react";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getInitials } from "@/lib/utils";

export interface UserCardProps {
  user: {
    readonly name: string;
    readonly email: string;
    readonly mode: "supabase" | "fixtures";
  };
  collapsed?: boolean;
}

export function UserCard({ user, collapsed = false }: UserCardProps) {
  const initials = getInitials(user.name);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`User menu for ${user.name}`}
          className={`group flex w-full cursor-pointer items-center gap-2.5 rounded-[var(--kit-radius-control,8px)] p-1.5 text-left transition-colors hover:bg-[var(--kit-bg-subtle,#f3f4f6)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--kit-accent,#1f6feb)] dark:hover:bg-[#21262d] ${
            collapsed ? "justify-center p-1" : ""
          }`}
        >
          <Avatar className="size-8 shrink-0 rounded-lg">
            <AvatarFallback className="rounded-lg bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] font-semibold text-[var(--kit-accent,#1f6feb)] text-xs dark:text-[#58a6ff]">
              {initials}
            </AvatarFallback>
          </Avatar>
          {!collapsed && (
            <div className="grid min-w-0 flex-1 leading-tight">
              <span className="truncate font-medium text-[var(--kit-text-primary,#111827)] text-xs dark:text-[#f0f6fc]">
                {user.name}
              </span>
              <span className="truncate text-[10px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
                {user.email}
              </span>
            </div>
          )}
          {!collapsed && (
            <ChevronsUpDown
              aria-hidden="true"
              className="size-3.5 shrink-0 text-[var(--kit-text-secondary,#6b7280)] transition-colors group-hover:text-[var(--kit-text-primary,#111827)] dark:text-[#8b949e] dark:group-hover:text-[#f0f6fc]"
            />
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={collapsed ? "center" : "end"}
        side="right"
        sideOffset={8}
        className="w-56 rounded-[var(--kit-radius-card,12px)] border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-1 text-xs shadow-[var(--kit-shadow-popover)] dark:border-[#30363d] dark:bg-[#161b22]"
      >
        <DropdownMenuLabel className="font-normal">
          <div className="truncate font-semibold text-[var(--kit-text-primary,#111827)] text-xs dark:text-[#f0f6fc]">
            {user.name}
          </div>
          <div className="truncate text-[10px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
            {user.email}
          </div>
          <div className="mt-1 text-[10px] text-[var(--kit-text-muted,#9ca3af)] dark:text-[#6e7681]">
            {user.mode === "fixtures" ? "Local development (fixtures)" : "Signed in with magic link"}
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator className="bg-[var(--kit-border,#e6e8ec)] dark:bg-[#30363d]" />
        <form action="/auth/signout" method="post">
          <DropdownMenuItem asChild>
            <button
              type="submit"
              className="flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[var(--kit-text-primary,#111827)] text-xs hover:bg-[var(--kit-bg-subtle,#f3f4f6)] dark:text-[#f0f6fc] dark:hover:bg-[#21262d]"
            >
              <LogOut className="size-3.5" />
              Sign out
            </button>
          </DropdownMenuItem>
        </form>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
