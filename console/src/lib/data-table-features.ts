import { rowPaginationFeature, rowSortingFeature, tableFeatures } from "@tanstack/react-table";

/**
 * TanStack Table v9 feature registry for every Console table. Pagination and sorting run on the server (the page URL
 * carries `page`, `size`, `sort`, `dir` and the database does the work), so only the state features are registered:
 * no client-side row models are shipped.
 */
export const dataTableFeatures = tableFeatures({
  rowPaginationFeature,
  rowSortingFeature,
});

export type DataTableFeatures = typeof dataTableFeatures;
