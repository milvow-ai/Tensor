import { notFound } from "next/navigation";

import type { Metadata } from "next";

import { PageHeader } from "@/components/kit";
import { attempt } from "@/lib/farm/data";
import { getMcpServerDetail } from "@/lib/farm/integrations-data";
import type { Connection } from "@/lib/farm/types";

import { McpDetailView } from "./_components/mcp-detail-view";

interface McpDetailPageProps {
  params: Promise<{ id: string }>;
}

export async function generateMetadata({ params }: McpDetailPageProps): Promise<Metadata> {
  const { id } = await params;
  return {
    title: `${id} MCP Server`,
    description: `Manage tools and accounts for the ${id} MCP server.`,
  };
}

export default async function McpDetailPage({ params }: McpDetailPageProps) {
  const { id } = await params;
  const detail = await getMcpServerDetail(id);

  if (!detail) {
    notFound();
  }
  const connectionsResult = await attempt(async (data) => (await data.listConnections(id, { pageSize: 50 })).items);
  const connections: Connection[] = connectionsResult.ok ? connectionsResult.data : [];

  return (
    <div className="space-y-6">
      <PageHeader title={detail.integration.name} subtitle={`MCP server · ${detail.integration.namespace}`} />
      <McpDetailView initialDetail={detail} connections={connections} />
    </div>
  );
}
