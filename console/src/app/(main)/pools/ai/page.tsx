import type { Metadata } from "next";

import { PoolsIndex } from "../_components/pools-index";

export const metadata: Metadata = { title: "AI Pools" };

export default function AiPoolsPage() {
  return <PoolsIndex kind="ai" />;
}
