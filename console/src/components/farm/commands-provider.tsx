"use client";

import { createContext, useContext } from "react";

import { useCommands } from "./use-command";

type CommandsApi = ReturnType<typeof useCommands>;

const CommandsContext = createContext<CommandsApi | null>(null);

/** One command tracker shared by every control on a page, so a dialog's command can show up in a table row. */
export function CommandsProvider({ children }: { children: React.ReactNode }) {
  const api = useCommands();
  return <CommandsContext.Provider value={api}>{children}</CommandsContext.Provider>;
}

export function useSharedCommands(): CommandsApi {
  const api = useContext(CommandsContext);
  if (!api) throw new Error("useSharedCommands must be used inside <CommandsProvider>.");
  return api;
}
