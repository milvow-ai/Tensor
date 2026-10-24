import { AlertTriangle } from "lucide-react";

export interface NeedsYouBannerProps {
  accountsNeedsLogin: number;
  openAlertsCount: number;
  budgetRatio: number | null;
}

export function NeedsYouBanner({
  accountsNeedsLogin,
  openAlertsCount,
  budgetRatio,
}: NeedsYouBannerProps) {
  const needsAttention =
    accountsNeedsLogin > 0 || openAlertsCount > 0 || (budgetRatio !== null && budgetRatio >= 0.8);

  if (!needsAttention) return null;

  const items: string[] = [];
  if (accountsNeedsLogin > 0) {
    items.push(`${accountsNeedsLogin} account${accountsNeedsLogin > 1 ? "s" : ""} need login`);
  }
  if (openAlertsCount > 0) {
    items.push(`${openAlertsCount} open alert${openAlertsCount > 1 ? "s" : ""}`);
  }
  if (budgetRatio !== null && budgetRatio >= 0.8) {
    items.push(`Budget at ${Math.round(budgetRatio * 100)}%`);
  }

  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-warning,#f59e0b)]/30 bg-[var(--kit-warning-soft,#fef3c7)] px-4 py-3 text-xs text-[var(--kit-warning-text,#b45309)] dark:bg-[rgba(210,153,34,0.15)] dark:text-[#e3b341]"
    >
      <div className="flex items-center gap-2">
        <AlertTriangle className="size-4 shrink-0 text-[var(--kit-warning,#f59e0b)]" />
        <span className="font-semibold">Needs you:</span>
        <span className="truncate">{items.join(" · ")}</span>
      </div>
      <a
        href="#alerts"
        className="font-semibold underline underline-offset-2 hover:opacity-80"
      >
        Review now →
      </a>
    </div>
  );
}
