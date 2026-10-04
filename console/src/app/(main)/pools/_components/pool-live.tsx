"use client";

import { useLiveRefresh } from "@/components/farm/use-command";

const POLL_MS = 5000;

/**
 * Keeps a pool page current: re-reads the server data every few seconds while the tab is visible, so account state,
 * usage and command results update without a reload. Mounted only on pool pages (polling, not Realtime: no extra
 * client bundle and no database publication to configure).
 */
export function PoolLive() {
  useLiveRefresh(POLL_MS);
  return null;
}
