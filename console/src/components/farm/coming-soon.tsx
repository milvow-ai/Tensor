import Link from "next/link";

import { cn } from "cn";
import { ArrowRight, Check, type LucideIcon } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";

import { PageHeader } from "./page-header";
import { SectionTitle } from "./section-title";
import { ToneBadge } from "./status";

export interface ComingSoonProps {
  title: string;
  description: string;
  milestone: "C2" | "C3";
  icon: LucideIcon;
  /** What the finished page will let the owner do. */
  will: string[];
  /** Where the same information can be found today. */
  today: { text: string; href: string; label: string };
}

/** A page that is not built yet: says so plainly, explains what it will do, and points to what works today. */
export function ComingSoon({ title, description, milestone, icon: Icon, will, today }: ComingSoonProps) {
  return (
    <div className="flex flex-col gap-4 md:gap-5">
      <PageHeader
        title={title}
        description={description}
        badge={<ToneBadge tone="muted">Arrives in {milestone}</ToneBadge>}
      />
      <div className="grid gap-4 md:gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="mb-1 flex size-9 items-center justify-center rounded-lg bg-muted text-muted-foreground">
              <Icon aria-hidden="true" className="size-4.5" />
            </div>
            <SectionTitle>This page is not built yet</SectionTitle>
            <CardDescription>
              It is planned for milestone {milestone}. Nothing on it is simulated. When it ships it will let you:
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-2.5">
              {will.map((item) => (
                <li key={item} className="flex items-start gap-2.5 text-sm">
                  <Check aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <SectionTitle>Available today</SectionTitle>
            <CardDescription>{today.text}</CardDescription>
          </CardHeader>
          <CardContent>
            <Link href={today.href} className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
              {today.label}
              <ArrowRight data-icon="inline-end" aria-hidden="true" />
            </Link>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
