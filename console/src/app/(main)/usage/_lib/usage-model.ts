import type { BillingOverview, CostPerResultRow, PoolOverviewRow, SpendMonthRow } from "@/lib/farm/types";

/** Daily spend rows reach back this many days (the Farm records no more). */
export const SPEND_WINDOW_DAYS = 90;

const DAY_MS = 86_400_000;
const SERIES_COLORS = ["#2563eb", "#d97706", "#16a34a", "#7c3aed", "#db2777", "#0891b2"];
const OTHER_COLOR = "#94a3b8";
const MAX_SERIES = 5;

export type PeriodKey = "7d" | "14d" | "30d" | "this-month" | "all";

export interface UsageSeries {
  key: string;
  name: string;
  color: string;
}

export interface UsageShareRow {
  label: string;
  value: number;
  share: number;
  color: string;
  sublabel?: string;
}

export interface UsageModel {
  period: PeriodKey;
  periodLabel: string;
  /** Days actually covered by the period (capped by what the Farm records). */
  days: number;
  spend: number;
  /** Spend in the equally long period before this one; null for "all" or when the window does not reach back that far. */
  previousSpend: number | null;
  deltaPct: number | null;
  chart: Array<Record<string, number | string>>;
  series: UsageSeries[];
  byAi: UsageShareRow[];
  byTool: UsageShareRow[];
  byDay: UsageShareRow[];
  byAccount: UsageShareRow[];
  month: SpendMonthRow | null;
}

export function parsePeriod(raw: string | undefined): PeriodKey {
  return raw === "14d" || raw === "30d" || raw === "this-month" || raw === "all" ? raw : "7d";
}

const PERIOD_LABELS: Record<PeriodKey, string> = {
  "7d": "last 7 days",
  "14d": "last 14 days",
  "30d": "last 30 days",
  "this-month": "this month",
  all: "all recorded days",
};

function periodDays(period: PeriodKey, now: Date): number {
  if (period === "7d") return 7;
  if (period === "14d") return 14;
  if (period === "30d") return 30;
  if (period === "this-month") return now.getUTCDate();
  return SPEND_WINDOW_DAYS;
}

const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

function toShares(totals: Map<string, { name: string; value: number }>, colors: Map<string, string>): UsageShareRow[] {
  const sum = [...totals.values()].reduce((acc, t) => acc + t.value, 0);
  return [...totals.entries()]
    .filter(([, t]) => t.value > 0)
    .sort((a, b) => b[1].value - a[1].value)
    .map(([key, t]) => ({
      label: t.name,
      value: t.value,
      share: sum > 0 ? (t.value / sum) * 100 : 0,
      color: colors.get(key) ?? OTHER_COLOR,
    }));
}

function accountRows(rows: CostPerResultRow[]): UsageShareRow[] {
  const sum = rows.reduce((acc, r) => acc + r.spend_usd, 0);
  return rows
    .filter((r) => r.spend_usd > 0)
    .sort((a, b) => b.spend_usd - a.spend_usd)
    .map((r, i) => ({
      label: r.connection_label,
      value: r.spend_usd,
      share: sum > 0 ? (r.spend_usd / sum) * 100 : 0,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      sublabel: r.provider_name,
    }));
}

export function buildUsage(
  overview: BillingOverview,
  pools: PoolOverviewRow[],
  period: PeriodKey,
  now: Date = new Date(),
): UsageModel {
  const days = periodDays(period, now);
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const from = iso(today - (days - 1) * DAY_MS);
  const prevFrom = iso(today - (2 * days - 1) * DAY_MS);
  const kindOf = new Map(pools.map((p) => [p.provider_id, p.kind]));

  const inPeriod = overview.spendDaily.filter((r) => r.day >= from);
  const previous = overview.spendDaily.filter((r) => r.day >= prevFrom && r.day < from);

  const perProvider = new Map<string, { name: string; value: number }>();
  const perDay = new Map<string, number>();
  for (const r of inPeriod) {
    const p = perProvider.get(r.provider_id) ?? { name: r.provider_name, value: 0 };
    p.value += r.spend_usd;
    perProvider.set(r.provider_id, p);
    perDay.set(r.day, (perDay.get(r.day) ?? 0) + r.spend_usd);
  }
  const spend = [...perProvider.values()].reduce((acc, p) => acc + p.value, 0);

  const previousSpend =
    period === "all" || 2 * days > SPEND_WINDOW_DAYS ? null : previous.reduce((acc, r) => acc + r.spend_usd, 0);
  const deltaPct =
    previousSpend !== null && previousSpend > 0 ? ((spend - previousSpend) / previousSpend) * 100 : null;

  const ranked = [...perProvider.entries()].sort((a, b) => b[1].value - a[1].value);
  const colors = new Map<string, string>();
  const series: UsageSeries[] = ranked.slice(0, MAX_SERIES).map(([key, p], i) => {
    colors.set(key, SERIES_COLORS[i]);
    return { key, name: p.name, color: SERIES_COLORS[i] };
  });
  const topKeys = new Set(series.map((s) => s.key));
  const hasOther = ranked.length > MAX_SERIES;
  if (hasOther) series.push({ key: "other", name: "Other", color: OTHER_COLOR });

  const chartByDay = new Map<string, Record<string, number | string>>();
  for (const r of inPeriod) {
    const row = chartByDay.get(r.day) ?? { date: r.day };
    const key = topKeys.has(r.provider_id) ? r.provider_id : "other";
    row[key] = Number(row[key] ?? 0) + r.spend_usd;
    chartByDay.set(r.day, row);
  }
  const chart = [...chartByDay.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)));

  const split = (kind: "ai" | "tool") =>
    toShares(new Map([...perProvider].filter(([id]) => kindOf.get(id) === kind)), colors);

  const dayTotals = new Map([...perDay].map(([day, value]) => [day, { name: day, value }]));
  const byDay = toShares(dayTotals, new Map()).slice(0, 10).map((r) => ({ ...r, color: SERIES_COLORS[0] }));

  return {
    period,
    periodLabel: PERIOD_LABELS[period],
    days,
    spend,
    previousSpend,
    deltaPct,
    chart,
    series,
    byAi: split("ai"),
    byTool: split("tool"),
    byDay,
    byAccount: accountRows(overview.costPerResult),
    month: overview.spendMonth.find((r) => r.provider_id === "total") ?? null,
  };
}
