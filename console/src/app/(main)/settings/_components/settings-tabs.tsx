import Link from "next/link";

import { SETTINGS_TABS, type SettingsTab } from "../_lib/tabs";

export function SettingsTabs({ active }: { active: SettingsTab }) {
  return (
    <nav
      aria-label="Settings sections"
      className="flex flex-wrap gap-1 border-[var(--kit-border,#e6e8ec)] border-b dark:border-[#30363d]"
    >
      {SETTINGS_TABS.map((tab) => {
        const selected = tab.id === active;
        return (
          <Link
            key={tab.id}
            href={tab.id === "workspace" ? "/settings" : `/settings?tab=${tab.id}`}
            aria-current={selected ? "page" : undefined}
            className={`-mb-px border-b-2 px-3 py-2 font-medium text-xs transition-colors ${
              selected
                ? "border-[var(--kit-accent,#1a63d6)] text-[var(--kit-accent,#1a63d6)]"
                : "border-transparent text-[var(--kit-text-secondary,#5b6370)] hover:text-[var(--kit-text-primary,#111827)]"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
