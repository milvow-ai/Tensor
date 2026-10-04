"use client";

import { type ComponentType, useEffect, useState } from "react";

/**
 * Loads a component module right after hydration and returns the component (null until it arrives).
 * Callers render a same-sized placeholder meanwhile, so heavy widgets stay out of the first-load bundle without
 * shifting the layout.
 */
export function useLazy<Props>(load: () => Promise<ComponentType<Props>>): ComponentType<Props> | null {
  const [component, setComponent] = useState<ComponentType<Props> | null>(null);
  useEffect(() => {
    let cancelled = false;
    void load().then((loaded) => {
      if (!cancelled) setComponent(() => loaded);
    });
    return () => {
      cancelled = true;
    };
  }, [load]);
  return component;
}
