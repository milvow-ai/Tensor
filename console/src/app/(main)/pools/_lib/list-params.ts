import type { ConnectionSortKey, SortDirection } from "@/lib/farm/types";

export const PAGE_SIZES = [5, 10, 25, 50] as const;
export const SORT_KEYS: readonly ConnectionSortKey[] = ["priority", "label", "status"];

export interface ListParams {
  page: number;
  pageSize: number;
  sort: ConnectionSortKey;
  dir: SortDirection;
}

type RawParams = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/** Reads the table state from the URL, falling back to defaults for anything missing or invalid. */
export function parseListParams(raw: RawParams): ListParams {
  const page = Number.parseInt(first(raw.page) ?? "1", 10);
  const size = Number.parseInt(first(raw.size) ?? "10", 10);
  const sort = first(raw.sort);
  const dir = first(raw.dir);
  return {
    page: Number.isFinite(page) && page >= 1 ? page : 1,
    pageSize: PAGE_SIZES.find((candidate) => candidate === size) ?? 10,
    sort: SORT_KEYS.find((candidate) => candidate === sort) ?? "priority",
    dir: dir === "desc" ? "desc" : "asc",
  };
}

/** Query string for a table state; defaults are omitted so the default view has a clean URL. */
export function toSearch(params: ListParams): string {
  const search = new URLSearchParams();
  if (params.page > 1) search.set("page", String(params.page));
  if (params.pageSize !== 10) search.set("size", String(params.pageSize));
  if (params.sort !== "priority" || params.dir !== "asc") {
    search.set("sort", params.sort);
    search.set("dir", params.dir);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
