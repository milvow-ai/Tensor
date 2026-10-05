import Link from "next/link";

import { ChevronRight } from "lucide-react";

export interface Crumb {
  label: string;
  href?: string;
}

/** Page title block: optional breadcrumb trail, title, one-line description and an actions slot. */
export function PageHeader({
  title,
  description,
  crumbs,
  actions,
  badge,
  icon: Icon,
}: {
  title: string;
  description?: React.ReactNode;
  crumbs?: Crumb[];
  actions?: React.ReactNode;
  badge?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        {crumbs && crumbs.length > 0 ? (
          <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-1 text-muted-foreground text-xs">
            {crumbs.map((crumb, index) => (
              <span key={crumb.label} className="flex items-center gap-1">
                {index > 0 ? <ChevronRight aria-hidden="true" className="size-3" /> : null}
                {crumb.href ? (
                  <Link
                    href={crumb.href}
                    className="rounded-sm hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {crumb.label}
                  </Link>
                ) : (
                  <span aria-current="page">{crumb.label}</span>
                )}
              </span>
            ))}
          </nav>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {Icon ? <Icon className="size-5 text-muted-foreground" aria-hidden="true" /> : null}
          <h1 className="font-semibold text-xl tracking-tight sm:text-2xl">{title}</h1>
          {badge}
        </div>
        {description ? <p className="max-w-3xl text-muted-foreground text-sm">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
