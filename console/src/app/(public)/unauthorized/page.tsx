import { ShieldAlert } from "lucide-react";
import type { Metadata } from "next";

import { SectionTitle } from "@/components/farm/section-title";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { checkOwner } from "@/lib/auth";

export const metadata: Metadata = { title: "Not authorized" };

export default async function UnauthorizedPage() {
  const result = await checkOwner();
  const email = result.status === "not_owner" ? result.email : null;

  return (
    <Card>
      <CardHeader className="items-center text-center">
        <div className="mb-1 flex size-10 items-center justify-center rounded-full bg-red-500/10 text-red-700 dark:text-red-400">
          <ShieldAlert aria-hidden="true" className="size-5" />
        </div>
        <SectionTitle level={1}>This Console is private</SectionTitle>
        <CardDescription>
          {email ? (
            <>
              <span className="font-medium text-foreground">{email}</span> is not the Farm owner, so there is nothing
              here for this account.
            </>
          ) : (
            "Only the Farm owner can open it. Sign in with the owner's email."
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form action="/auth/signout" method="post">
          <Button type="submit" variant="outline" className="w-full">
            Sign out and use another email
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
