"use client";

import dynamic from "next/dynamic";

import { Settings } from "lucide-react";

import { Button } from "@/components/ui/button";

// Layout, font and theme-preset options are rarely used; the popover and its controls load after first paint.
const LayoutControls = dynamic(() => import("./layout-controls").then((module) => module.LayoutControls), {
  ssr: false,
  loading: () => (
    <Button size="icon" variant="outline" disabled aria-label="Layout settings">
      <Settings aria-hidden="true" />
    </Button>
  ),
});

export function LayoutControlsLazy() {
  return <LayoutControls />;
}
