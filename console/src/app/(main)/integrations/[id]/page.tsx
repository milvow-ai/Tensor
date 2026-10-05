import { notFound } from "next/navigation";

import { Server } from "lucide-react";
import type { Metadata } from "next";

import { PageHeader } from "@/components/farm/page-header";
import { getMcpServerDetail } from "@/lib/farm/integrations-data";

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

  return (
    <div className="space-y-6">
      <PageHeader
        title={`${detail.integration.name} MCP Server`}
        description={`Configure exposed tools, account credentials, and synchronization for ${detail.integration.namespace}.`}
        icon={Server}
      />
      <McpDetailView initialDetail={detail} />
    </div>
  );
}
