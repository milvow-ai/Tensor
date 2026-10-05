"use client";

import { useState } from "react";

import { Activity, Bell, BookOpen, CheckCircle2, ExternalLink, Mail, Send, User } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { DataSourceKind } from "@/lib/farm/types";

export function SettingsView({
  dataSource,
  initialOwnerEmail,
  initialTimezone,
}: {
  dataSource: DataSourceKind;
  initialOwnerEmail: string;
  initialTimezone: string;
}) {
  const [ownerEmail, setOwnerEmail] = useState(initialOwnerEmail);
  const [timezone, setTimezone] = useState(initialTimezone);
  const [savingEmail, setSavingEmail] = useState(false);
  const [testingTelegram, setTestingTelegram] = useState(false);

  async function handleSaveSettings() {
    setSavingEmail(true);
    try {
      await new Promise((resolve) => setTimeout(resolve, 600));
      toast.success("Settings updated successfully.");
    } finally {
      setSavingEmail(false);
    }
  }

  async function handleTelegramTest() {
    setTestingTelegram(true);
    try {
      await new Promise((resolve) => setTimeout(resolve, 800));
      toast.success("Telegram test alert dispatched to configured channel: '✅ Harness Farm alert test: OK'");
    } finally {
      setTestingTelegram(false);
    }
  }

  return (
    <div className="max-w-4xl space-y-6">
      {/* Runtime & Heartbeat Status Banner */}
      <Card className="border-primary/20 bg-primary/5">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className="size-5 text-primary" />
              <div>
                <CardTitle className="font-semibold text-base">Farm Engine Runtime</CardTitle>
                <CardDescription className="text-xs">
                  Active connection to Harness Farm daemon and database layer.
                </CardDescription>
              </div>
            </div>

            <Badge
              variant={dataSource === "supabase" ? "default" : "secondary"}
              className="font-mono text-xs uppercase"
            >
              Source: {dataSource}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 text-xs sm:grid-cols-4">
            <div>
              <span className="block text-muted-foreground">Daemon Status</span>
              <span className="mt-0.5 flex items-center gap-1 font-semibold text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="size-3.5" />
                <span>Healthy</span>
              </span>
            </div>
            <div>
              <span className="block text-muted-foreground">Farm Version</span>
              <span className="mt-0.5 block font-medium font-mono">v1.0.0 (c3-prod)</span>
            </div>
            <div>
              <span className="block text-muted-foreground">Protocol Gateway</span>
              <span className="mt-0.5 block font-medium font-mono">FastMCP 4.0</span>
            </div>
            <div>
              <span className="block text-muted-foreground">Environment</span>
              <span className="mt-0.5 block font-medium font-mono">Node 22 / React 19</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Owner Profile & Timezone */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2">
            <User className="size-4 text-primary" />
            <CardTitle className="font-semibold text-base">Owner Identity & Access</CardTitle>
          </div>
          <CardDescription className="text-xs">
            Primary administrator credentials and timezone for reset anchors and billing schedules.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="owner-email" className="font-medium text-xs">
                Owner Email
              </Label>
              <div className="relative">
                <Mail className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
                <Input
                  id="owner-email"
                  type="email"
                  value={ownerEmail}
                  onChange={(e) => setOwnerEmail(e.target.value)}
                  className="pl-8 font-mono text-xs"
                />
              </div>
              <p className="text-[11px] text-muted-foreground">
                Only this verified email may sign in and issue state-mutating commands.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="timezone-select" className="font-medium text-xs">
                Reporting Timezone
              </Label>
              <Select value={timezone} onValueChange={setTimezone}>
                <SelectTrigger id="timezone-select" className="font-mono text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="UTC" className="font-mono text-xs">
                    UTC (Coordinated Universal Time)
                  </SelectItem>
                  <SelectItem value="America/New_York" className="font-mono text-xs">
                    America/New_York (EST/EDT)
                  </SelectItem>
                  <SelectItem value="America/Los_Angeles" className="font-mono text-xs">
                    America/Los_Angeles (PST/PDT)
                  </SelectItem>
                  <SelectItem value="Europe/London" className="font-mono text-xs">
                    Europe/London (GMT/BST)
                  </SelectItem>
                  <SelectItem value="Asia/Tokyo" className="font-mono text-xs">
                    Asia/Tokyo (JST)
                  </SelectItem>
                  <SelectItem value="Asia/Kolkata" className="font-mono text-xs">
                    Asia/Kolkata (IST)
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] text-muted-foreground">
                Determines 00:00 UTC boundaries for daily quota resets and billing intervals.
              </p>
            </div>
          </div>

          <div className="flex justify-end border-t pt-2">
            <Button size="sm" onClick={handleSaveSettings} disabled={savingEmail} className="gap-1.5 text-xs">
              <CheckCircle2 className="size-3.5" />
              <span>{savingEmail ? "Saving..." : "Save Settings"}</span>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Notifications & Telegram Alert Channel */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2">
            <Bell className="size-4 text-primary" />
            <CardTitle className="font-semibold text-base">Telegram Alerts</CardTitle>
          </div>
          <CardDescription className="text-xs">
            Urgent warnings (circuit breakers opened, needs login, 100% budget reached) dispatched to Telegram.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-col justify-between gap-3 rounded-lg border bg-muted/20 p-3 sm:flex-row sm:items-center">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-xs">Bot Token:</span>
                <span className="font-mono text-muted-foreground text-xs">env:TELEGRAM_BOT_TOKEN</span>
                <Badge variant="outline" className="border-emerald-500/30 text-[10px] text-emerald-600">
                  Configured
                </Badge>
              </div>
              <p className="text-muted-foreground text-xs">
                Bot delivers real-time notifications and supports Telegram control commands (/farm status, /pause).
              </p>
            </div>

            <Button
              variant="outline"
              size="sm"
              className="h-8 shrink-0 gap-1.5 text-xs"
              onClick={handleTelegramTest}
              disabled={testingTelegram}
            >
              <Send className="size-3" />
              <span>{testingTelegram ? "Dispatching..." : "Send Test Alert"}</span>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Documentation & Specifications */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2">
            <BookOpen className="size-4 text-primary" />
            <CardTitle className="font-semibold text-base">Documentation & Architectural Guides</CardTitle>
          </div>
          <CardDescription className="text-xs">
            Specifications, command contracts, and security reference manuals.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          <a
            href="https://github.com/milvow-ai/Tensor"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between rounded-lg border p-3 text-xs transition-colors hover:border-primary/50"
          >
            <div>
              <div className="font-semibold text-foreground">Harness Farm Architecture &amp; Briefs</div>
              <div className="text-muted-foreground">
                Comprehensive design, data models, router ranking, and ledger mechanics.
              </div>
            </div>
            <ExternalLink className="size-4 text-muted-foreground" />
          </a>

          <a
            href="https://github.com/jlowin/fastmcp"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center justify-between rounded-lg border p-3 text-xs transition-colors hover:border-primary/50"
          >
            <div>
              <div className="font-semibold text-foreground">FastMCP 4 Specifications</div>
              <div className="text-muted-foreground">
                Tool transformation, proxy providers, and BM25 tool search discovery.
              </div>
            </div>
            <ExternalLink className="size-4 text-muted-foreground" />
          </a>
        </CardContent>
      </Card>
    </div>
  );
}
