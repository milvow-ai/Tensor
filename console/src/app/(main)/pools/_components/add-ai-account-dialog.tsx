"use client";

import type { AiCli } from "@/lib/farm/command-meta";

import { AddAccountsDialog } from "./add-accounts-dialog";

export function AddAiAccountDialog({ defaultCli = "claude" }: { defaultCli?: AiCli; suggestedId?: string }) {
  return <AddAccountsDialog defaultProviderId={defaultCli} />;
}
