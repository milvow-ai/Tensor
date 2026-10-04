"use client";

import { type FormEvent, useId, useState } from "react";

import { MailCheck } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function LoginForm({
  next,
  configured,
  initialError,
}: {
  next: string;
  configured: boolean;
  initialError: string | null;
}) {
  const inputId = useId();
  const [email, setEmail] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(initialError);
  const [sending, setSending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const address = email.trim();
    if (!address) return setFieldError("Enter your email");
    if (!EMAIL.test(address)) return setFieldError("That does not look like an email address");
    setFieldError(null);
    setError(null);
    setSending(true);
    try {
      // The Supabase browser client is only needed here, so it loads on submit and stays out of the page bundle.
      const { createClient } = await import("@/lib/supabase/client");
      const redirect = `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;
      const { error: otpError } = await createClient().auth.signInWithOtp({
        email: address,
        options: { emailRedirectTo: redirect, shouldCreateUser: false },
      });
      // An address Supabase does not know is reported like success, so the page does not reveal who is registered.
      const unknownAddress = otpError?.status === 422 || /signups? not allowed/i.test(otpError?.message ?? "");
      if (otpError && !unknownAddress) {
        setError(
          otpError.status === 429
            ? "Too many requests. Wait a minute and try again."
            : "Could not send the link. Try again in a moment.",
        );
        return;
      }
      setSentTo(address);
    } catch {
      setError("Could not reach the sign-in service. Check the connection and try again.");
    } finally {
      setSending(false);
    }
  }

  if (!configured) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Sign-in is not configured</AlertTitle>
        <AlertDescription>
          Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY for this deployment.
        </AlertDescription>
      </Alert>
    );
  }

  if (sentTo) {
    return (
      <div role="status" className="flex flex-col items-center gap-3 py-2 text-center">
        <div className="flex size-10 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
          <MailCheck aria-hidden="true" className="size-5" />
        </div>
        <div className="space-y-1">
          <p className="font-medium text-sm">Check your inbox</p>
          <p className="text-muted-foreground text-sm">
            If {sentTo} is the owner's address, a sign-in link is on its way. It works once and expires soon.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setSentTo(null)}>
          Use a different email
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {error ? (
        <Alert variant="destructive" role="alert">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <Field data-invalid={Boolean(fieldError)}>
        <FieldLabel htmlFor={inputId}>Email</FieldLabel>
        <Input
          id={inputId}
          type="email"
          name="email"
          autoComplete="email"
          inputMode="email"
          placeholder="owner@example.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          aria-invalid={Boolean(fieldError)}
        />
        {fieldError ? (
          <FieldError>{fieldError}</FieldError>
        ) : (
          <FieldDescription>Only the Farm owner's email can sign in.</FieldDescription>
        )}
      </Field>
      <Button type="submit" disabled={sending}>
        {sending ? <Spinner data-icon="inline-start" /> : null}
        Send magic link
      </Button>
    </form>
  );
}
