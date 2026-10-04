import type { Metadata } from "next";

import { PoolDetailView } from "../_components/pool-detail";
import { parseListParams } from "../_lib/list-params";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  return { title: id };
}

export default async function ToolPoolPage({ params, searchParams }: Props) {
  const [{ id }, search] = await Promise.all([params, searchParams]);
  return <PoolDetailView id={id} kind="tool" params={parseListParams(search)} />;
}
