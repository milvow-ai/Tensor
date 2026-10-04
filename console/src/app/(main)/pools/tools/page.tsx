import type { Metadata } from "next";

import { PoolsIndex } from "../_components/pools-index";

export const metadata: Metadata = { title: "Tools & Pools" };

export default function ToolPoolsPage() {
  return <PoolsIndex kind="tool" />;
}
