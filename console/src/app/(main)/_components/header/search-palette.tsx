"use client";

import { useRouter } from "next/navigation";

import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { sidebarItems } from "@/navigation/sidebar/sidebar-items";

/** The page list behind Ctrl+K. Loaded the first time it is opened. */
export function SearchPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <Command>
        <CommandInput placeholder="Go to a page…" />
        <CommandList>
          <CommandEmpty>No page matches.</CommandEmpty>
          {sidebarItems.map((group) => (
            <CommandGroup key={group.id} heading={group.label}>
              {group.items.map((item) => (
                <CommandItem
                  key={item.id}
                  value={`${group.label ?? ""} ${item.title}`}
                  onSelect={() => {
                    onOpenChange(false);
                    router.push(item.url);
                  }}
                >
                  {item.icon ? <item.icon aria-hidden="true" /> : null}
                  <span>{item.title}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          ))}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
