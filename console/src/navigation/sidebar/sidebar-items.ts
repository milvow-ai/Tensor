import {
  Activity,
  Blocks,
  Bot,
  CreditCard,
  Database,
  GitFork,
  LayoutDashboard,
  type LucideIcon,
  Settings,
  ShieldCheck,
  Wrench,
} from "lucide-react";

export interface NavMainItem {
  id: string;
  title: string;
  url: string;
  icon?: LucideIcon;
  /** Shown as a small tag in the sidebar; pages that arrive in a later milestone carry "Soon". */
  badge?: "Soon";
}

export interface NavGroup {
  id: number;
  label?: string;
  items: NavMainItem[];
}

export const sidebarItems: NavGroup[] = [
  {
    id: 1,
    label: "Operate",
    items: [
      { id: "overview", title: "Overview", url: "/overview", icon: LayoutDashboard },
      { id: "tools-pools", title: "Tools & Pools", url: "/pools/tools", icon: Wrench },
      { id: "ai-pools", title: "AI Pools", url: "/pools/ai", icon: Bot },
      { id: "runs", title: "Runs", url: "/runs", icon: Activity, badge: "Soon" },
    ],
  },
  {
    id: 2,
    label: "Control",
    items: [
      { id: "routing", title: "Routing", url: "/routing", icon: GitFork, badge: "Soon" },
      { id: "policies", title: "Policies & Budgets", url: "/policies", icon: ShieldCheck, badge: "Soon" },
      { id: "billing", title: "Billing", url: "/billing", icon: CreditCard, badge: "Soon" },
    ],
  },
  {
    id: 3,
    label: "Data",
    items: [{ id: "memory", title: "Memory & Evidence", url: "/memory", icon: Database, badge: "Soon" }],
  },
  {
    id: 4,
    label: "Setup",
    items: [
      { id: "integrations", title: "Integrations", url: "/integrations", icon: Blocks, badge: "Soon" },
      { id: "settings", title: "Settings", url: "/settings", icon: Settings, badge: "Soon" },
    ],
  },
];
