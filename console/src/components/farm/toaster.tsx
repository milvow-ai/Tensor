"use client";

import { Toaster } from "@/components/ui/sonner";
import { usePreferencesStore } from "@/stores/preferences/preferences-provider";

/** Sonner toaster that follows the Console's own theme setting (the starter's Toaster only reads next-themes). */
export function FarmToaster() {
  const mode = usePreferencesStore((state) => state.resolvedThemeMode);
  return <Toaster theme={mode} position="bottom-right" closeButton />;
}
