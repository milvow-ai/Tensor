import React from "react";

import ReactDOMServer from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/overview",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next/font/google", () => ({
  Geist: () => ({ variable: "--font-geist", style: { fontFamily: "Geist" } }),
  Geist_Mono: () => ({ variable: "--font-geist-mono", style: { fontFamily: "Geist Mono" } }),
  Inter: () => ({ variable: "--font-inter", style: { fontFamily: "Inter" } }),
  JetBrains_Mono: () => ({ variable: "--font-jetbrains-mono", style: { fontFamily: "JetBrains Mono" } }),
}));

import { DataSourceBadge } from "../../../app/(main)/_components/header/data-source-badge";
import { LayoutControls } from "../../../app/(main)/_components/header/layout-controls";
import { AppSidebar } from "../../../app/(main)/_components/sidebar/app-sidebar";
import { NavMain } from "../../../app/(main)/_components/sidebar/nav-main";
import { NavUserMenu } from "../../../app/(main)/_components/sidebar/nav-user-menu";
import { BillingKpiRow } from "../../../app/(main)/billing/_components/billing-kpi-row";
import { StateDot } from "../../../components/farm/status";
import { SidebarMenuButton, SidebarProvider } from "../../../components/ui/sidebar";
import { TooltipProvider } from "../../../components/ui/tooltip";
import { PREFERENCE_DEFAULTS } from "../../../lib/preferences/preferences-config";
import { sidebarItems } from "../../../navigation/sidebar/sidebar-items";
import { PreferencesStoreProvider } from "../../../stores/preferences/preferences-provider";

describe("Layout and Sidebar components (Radix Slot regression guard)", () => {
  it("renders DataSourceBadge without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(TooltipProvider, null, React.createElement(DataSourceBadge, { mode: "fixtures" })),
      );
    }).not.toThrow();
  });

  it("renders StateDot without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(TooltipProvider, null, React.createElement(StateDot, { state: "active", label: "test" })),
      );
    }).not.toThrow();
  });

  it("renders NavMain in expanded mode without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(
          SidebarProvider,
          { defaultOpen: true },
          React.createElement(TooltipProvider, null, React.createElement(NavMain, { groups: sidebarItems })),
        ),
      );
    }).not.toThrow();
  });

  it("renders NavMain in collapsed mode without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(
          SidebarProvider,
          { defaultOpen: false },
          React.createElement(TooltipProvider, null, React.createElement(NavMain, { groups: sidebarItems })),
        ),
      );
    }).not.toThrow();
  });

  it("renders SidebarMenuButton asChild with tooltip in both expanded and collapsed modes", () => {
    for (const open of [true, false]) {
      expect(() => {
        ReactDOMServer.renderToString(
          React.createElement(
            SidebarProvider,
            { defaultOpen: open },
            React.createElement(
              TooltipProvider,
              null,
              React.createElement(
                SidebarMenuButton,
                { asChild: true, tooltip: "Overview" },
                React.createElement(
                  "a",
                  { href: "/overview" },
                  React.createElement("span", null, "Icon"),
                  React.createElement("span", null, "Overview"),
                ),
              ),
            ),
          ),
        );
      }).not.toThrow();
    }
  });

  it("renders AppSidebar without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(
          PreferencesStoreProvider,
          { initialValues: PREFERENCE_DEFAULTS },
          React.createElement(
            SidebarProvider,
            null,
            React.createElement(
              TooltipProvider,
              null,
              React.createElement(AppSidebar, {
                user: { name: "Owner", email: "owner@test.com", mode: "fixtures" },
              }),
            ),
          ),
        ),
      );
    }).not.toThrow();
  });

  it("renders NavUserMenu without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(
          SidebarProvider,
          null,
          React.createElement(
            TooltipProvider,
            null,
            React.createElement(NavUserMenu, {
              user: { name: "Owner", email: "owner@test.com", mode: "fixtures" },
            }),
          ),
        ),
      );
    }).not.toThrow();
  });

  it("renders LayoutControls without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(
          PreferencesStoreProvider,
          { initialValues: PREFERENCE_DEFAULTS },
          React.createElement(TooltipProvider, null, React.createElement(LayoutControls)),
        ),
      );
    }).not.toThrow();
  });

  it("renders BillingKpiRow without slot errors", () => {
    expect(() => {
      ReactDOMServer.renderToString(
        React.createElement(BillingKpiRow, {
          totalSpendRow: {
            provider_id: "total",
            provider_name: "Total",
            kind: "all",
            usage_usd: 120,
            billing_usd: 0,
            spend_usd: 120,
            budget_usd: 500,
            forecast_usd: 240,
            elapsed_days: 15,
            days_in_month: 30,
          },
          paidAccountsCount: 5,
          idlePaidCount: 1,
        }),
      );
    }).not.toThrow();
  });
});
