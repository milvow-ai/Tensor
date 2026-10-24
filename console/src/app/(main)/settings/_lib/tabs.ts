export const SETTINGS_TABS = [
  { id: "workspace", label: "Workspace" },
  { id: "members", label: "Members" },
  { id: "policies", label: "Policies & budgets" },
  { id: "routing", label: "Routing" },
  { id: "prices", label: "Prices" },
  { id: "notifications", label: "Notifications" },
] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number]["id"];

export function parseSettingsTab(raw: string | undefined): SettingsTab {
  return SETTINGS_TABS.find((t) => t.id === raw)?.id ?? "workspace";
}
