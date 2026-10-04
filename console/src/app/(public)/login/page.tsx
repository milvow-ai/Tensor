import Link from "next/link";

import { cn } from "cn";
import { FlaskConical } from "lucide-react";
import type { Metadata } from "next";

import { SectionTitle } from "@/components/farm/section-title";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { resolveDataSource } from "@/lib/farm/mode";
import { readSupabaseEnv } from "@/lib/supabase/env";

import { LoginForm } from "./_components/login-form";

export const metadata: Metadata = { title: "Sign in" };

const ERRORS: Record<string, string> = {
  link: "That sign-in link is invalid or has expired. Request a new one.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const next = typeof params.next === "string" ? params.next : "/overview";
  const error = typeof params.error === "string" ? (ERRORS[params.error] ?? null) : null;

  if (resolveDataSource() === "fixtures") {
    return (
      <Card>
        <CardHeader>
          <SectionTitle level={1}>Sign-in is skipped in fixtures mode</SectionTitle>
          <CardDescription>
            This server runs with FARM_DATA_SOURCE=fixtures (local development), so there is no login and all data is
            demo data.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-start gap-2 rounded-lg border bg-muted/50 p-3 text-muted-foreground text-xs">
            <FlaskConical aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
            In Supabase mode this page sends a magic link to the owner's email and nothing else.
          </div>
          <Link href="/overview" className={cn(buttonVariants({ variant: "default" }), "w-full")}>
            Continue to the Console
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <SectionTitle level={1}>Sign in</SectionTitle>
        <CardDescription>Enter the owner's email. We send a one-time link; there is no password.</CardDescription>
      </CardHeader>
      <CardContent>
        <LoginForm next={next} configured={readSupabaseEnv() !== null} initialError={error} />
      </CardContent>
    </Card>
  );
}
