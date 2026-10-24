"use client";

import type React from "react";
import { forwardRef, useState } from "react";

import { Check, CheckCircle2, Copy, Play, ShieldCheck, XCircle } from "lucide-react";

export interface AppInfo {
  name: string;
  id: string;
  icon?: React.ReactNode;
  description?: string;
}

export type ConnectionTestStatus = "idle" | "testing" | "success" | "error";

export interface ConnectAppCardProps extends React.HTMLAttributes<HTMLDivElement> {
  app: AppInfo;
  configSnippet: string;
  permissionProfile: string;
  onTestConnection: () => Promise<boolean> | undefined;
  testStatus?: ConnectionTestStatus;
  testMessage?: string;
  loading?: boolean;
}

export const ConnectAppCard = forwardRef<HTMLDivElement, ConnectAppCardProps>(
  (
    {
      app,
      configSnippet,
      permissionProfile,
      onTestConnection,
      testStatus: controlledStatus,
      testMessage: controlledMessage,
      loading = false,
      className = "",
      ...props
    },
    ref,
  ) => {
    const [copied, setCopied] = useState(false);
    const [internalStatus, setInternalStatus] = useState<ConnectionTestStatus>("idle");
    const [internalMessage, setInternalMessage] = useState<string>("");

    const status = controlledStatus ?? internalStatus;
    const message = controlledMessage ?? internalMessage;

    const handleCopy = () => {
      navigator.clipboard?.writeText(configSnippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    };

    const handleTest = async () => {
      setInternalStatus("testing");
      setInternalMessage("Testing connection to Farm daemon...");
      try {
        const result = await onTestConnection();
        if (result === false) {
          setInternalStatus("error");
          setInternalMessage("Daemon connection failed or refused token.");
        } else {
          setInternalStatus("success");
          setInternalMessage("Connected successfully. Ready for agent calls.");
        }
      } catch (err) {
        setInternalStatus("error");
        setInternalMessage(err instanceof Error ? err.message : "Connection failed");
      }
    };

    if (loading) {
      return (
        <div
          ref={ref}
          className={`animate-pulse space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
          {...props}
        >
          <div className="flex gap-3">
            <div className="h-8 w-8 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            <div className="flex-1 space-y-1.5">
              <div className="h-4 w-32 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
              <div className="h-3 w-48 rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
            </div>
          </div>
          <div className="h-20 w-full rounded bg-[var(--kit-bg-subtle,#f3f4f6)] dark:bg-[#21262d]" />
        </div>
      );
    }

    return (
      <div
        ref={ref}
        className={`space-y-4 rounded-[var(--kit-radius-card,12px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-card,#ffffff)] p-5 text-xs shadow-[var(--kit-shadow-card)] dark:border-[#30363d] dark:bg-[#161b22] ${className}`}
        {...props}
      >
        {/* App Title & Description */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            {app.icon ? (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-bg-subtle,#f3f4f6)] text-[var(--kit-text-primary,#111827)] dark:bg-[#21262d] dark:text-[#f0f6fc]">
                {app.icon}
              </div>
            ) : (
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-accent-soft,rgba(31,111,235,0.1))] font-bold text-[var(--kit-accent,#1f6feb)] text-xs dark:text-[#58a6ff]">
                {app.name.slice(0, 2).toUpperCase()}
              </div>
            )}
            <div>
              <h3 className="font-semibold text-[var(--kit-text-primary,#111827)] text-sm dark:text-[#f0f6fc]">
                {app.name}
              </h3>
              {app.description && (
                <p className="text-[11px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
                  {app.description}
                </p>
              )}
            </div>
          </div>
        </div>

        {/* Config Snippet */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-[11px] text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">
              Configuration snippet:
            </span>
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex cursor-pointer items-center gap-1 text-[11px] text-[var(--kit-accent,#1f6feb)] hover:underline dark:text-[#58a6ff]"
            >
              {copied ? (
                <>
                  <Check className="h-3 w-3 text-[var(--kit-positive,#16a34a)]" />
                  <span>Copied!</span>
                </>
              ) : (
                <>
                  <Copy className="h-3 w-3" />
                  <span>Copy config</span>
                </>
              )}
            </button>
          </div>

          <div className="overflow-x-auto whitespace-pre rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-2.5 font-mono text-[11px] text-[var(--kit-text-primary,#111827)] dark:border-[#30363d] dark:bg-[#0d1117] dark:text-[#f0f6fc]">
            {configSnippet}
          </div>
        </div>

        {/* Permission Profile */}
        <div className="flex items-center gap-2 rounded-[var(--kit-radius-control,8px)] border border-[var(--kit-border,#e6e8ec)] bg-[var(--kit-bg-subtle,#f3f4f6)] p-2 dark:border-[#30363d] dark:bg-[#21262d]">
          <ShieldCheck className="h-4 w-4 shrink-0 text-[var(--kit-accent,#1f6feb)] dark:text-[#58a6ff]" />
          <div className="truncate text-[11px]">
            <span className="text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">Permission profile:</span>{" "}
            <span className="font-medium text-[var(--kit-text-primary,#111827)] dark:text-[#f0f6fc]">
              {permissionProfile}
            </span>
          </div>
        </div>

        {/* Test Connection Button & Status */}
        <div className="flex flex-col justify-between gap-2.5 border-[var(--kit-border,#e6e8ec)] border-t pt-2 sm:flex-row sm:items-center dark:border-[#30363d]">
          <button
            type="button"
            onClick={handleTest}
            disabled={status === "testing"}
            className="inline-flex cursor-pointer select-none items-center justify-center gap-1.5 rounded-[var(--kit-radius-control,8px)] bg-[var(--kit-accent,#1f6feb)] px-3 py-1.5 font-medium text-white shadow-sm transition-colors hover:bg-[var(--kit-accent-hover,#195ac0)] disabled:opacity-50"
          >
            <Play className={`h-3.5 w-3.5 ${status === "testing" ? "animate-spin" : ""}`} />
            <span>{status === "testing" ? "Testing..." : "Test connection"}</span>
          </button>

          {status !== "idle" && (
            <div className="flex items-center gap-1.5 text-[11px]">
              {status === "success" && (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5 text-[var(--kit-positive-text,#15803d)] dark:text-[#56d364]" />
                  <span className="font-medium text-[var(--kit-positive-text,#15803d)] dark:text-[#56d364]">
                    {message}
                  </span>
                </>
              )}
              {status === "error" && (
                <>
                  <XCircle className="h-3.5 w-3.5 text-[var(--kit-negative-text,#b91c1c)] dark:text-[#ff7b72]" />
                  <span className="font-medium text-[var(--kit-negative-text,#b91c1c)] dark:text-[#ff7b72]">
                    {message}
                  </span>
                </>
              )}
              {status === "testing" && (
                <span className="text-[var(--kit-text-secondary,#6b7280)] dark:text-[#8b949e]">{message}</span>
              )}
            </div>
          )}
        </div>
      </div>
    );
  },
);

ConnectAppCard.displayName = "ConnectAppCard";
