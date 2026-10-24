"use client";

import { Suspense, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { CommandSearchButton, NotificationsBell, PeriodSelect } from "@/components/kit";

const loadPalette = () =>
  import("./header/search-palette").then((module) => module.SearchPalette);
const SearchPalette = dynamic(loadPalette, { ssr: false });

export function TopBarSearch() {
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setMounted(true);
        setOpen((curr) => !curr);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <CommandSearchButton
        onClick={() => {
          setMounted(true);
          setOpen(true);
        }}
        onPointerEnter={() => void loadPalette()}
        onFocus={() => void loadPalette()}
        className="w-40 sm:w-56 md:w-64"
      />
      {mounted ? <SearchPalette open={open} onOpenChange={setOpen} /> : null}
    </>
  );
}

function PeriodSelectInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const period = searchParams.get("period") ?? "7d";

  const handlePeriodChange = (newPeriod: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("period", newPeriod);
    router.push(`${pathname}?${params.toString()}`);
  };

  return <PeriodSelect value={period} onChange={handlePeriodChange} className="w-32 sm:w-36" />;
}

export function TopBarPeriodSelect() {
  return (
    <Suspense fallback={<PeriodSelect value="7d" onChange={() => { /* fallback */ }} loading className="w-32 sm:w-36" />}>
      <PeriodSelectInner />
    </Suspense>
  );
}

export function LiveDot() {
  return (
    <div
      role="status"
      aria-label="Live data streaming active"
      className="flex items-center gap-1.5 px-2 py-1 text-xs text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]"
    >
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
      </span>
      <span className="font-semibold text-[11px] text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
        live
      </span>
    </div>
  );
}

export function TopBarNotifications({ count }: { count: number }) {
  return (
    <NotificationsBell
      count={count}
      onClick={() => {
        if (typeof window !== "undefined") {
          window.location.href = "/overview#alerts";
        }
      }}
    />
  );
}
