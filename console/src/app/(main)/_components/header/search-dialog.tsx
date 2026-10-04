"use client";

import { useEffect, useState } from "react";

import dynamic from "next/dynamic";

import { Search } from "lucide-react";

import { Button } from "@/components/ui/button";

// cmdk is about 25 kB gzipped and only needed once the palette is opened.
const loadPalette = () => import("./search-palette").then((module) => module.SearchPalette);
const SearchPalette = dynamic(loadPalette, { ssr: false });

/** Jump to any page: Ctrl or Cmd + K. */
export function SearchDialog() {
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setMounted(true);
        setOpen((current) => !current);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <Button
        onClick={() => {
          setMounted(true);
          setOpen(true);
        }}
        onPointerEnter={() => void loadPalette()}
        onFocus={() => void loadPalette()}
        variant="ghost"
        size="sm"
        aria-label="Search pages"
        className="px-1.5 font-normal text-muted-foreground sm:px-2"
      >
        <Search data-icon="inline-start" aria-hidden="true" />
        <span className="hidden sm:inline">Go to page</span>
        <kbd className="hidden h-5 select-none items-center rounded border bg-muted px-1.5 font-medium text-[10px] text-foreground/80 sm:inline-flex">
          Ctrl K
        </kbd>
      </Button>
      {mounted ? <SearchPalette open={open} onOpenChange={setOpen} /> : null}
    </>
  );
}
