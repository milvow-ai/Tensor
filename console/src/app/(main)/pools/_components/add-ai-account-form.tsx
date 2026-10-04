"use client";

import { useId, useState } from "react";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { useSharedCommands } from "@/components/farm/commands-provider";
import { CopyCommand } from "@/components/farm/copy-command";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Spinner } from "@/components/ui/spinner";
import { AI_CLI_INFO, AI_CLIS, type AiCli } from "@/lib/farm/command-meta";
import { type AddAiAccountFormValues, addAiAccountFormSchema } from "@/lib/farm/commands";

export function AddAiAccountForm({
  defaultCli = "claude",
  suggestedId,
  open,
  onOpenChange,
}: {
  defaultCli?: AiCli;
  suggestedId?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const { run } = useSharedCommands();
  const uid = useId();
  const idFor = (name: string) => `${uid}-${name}`;

  const form = useForm<AddAiAccountFormValues>({
    resolver: zodResolver(addAiAccountFormSchema),
    mode: "onChange",
    defaultValues: {
      cli: defaultCli,
      id: suggestedId ?? "",
      label: "",
      models: AI_CLI_INFO[defaultCli].models.join(", "),
      planName: "",
      planPrice: 0,
    },
  });
  const { errors, isSubmitting } = form.formState;
  const cli = form.watch("cli");
  const id = form.watch("id");

  async function onSubmit(values: AddAiAccountFormValues) {
    setFormError(null);
    const models = values.models
      .split(",")
      .map((model) => model.trim())
      .filter(Boolean);
    const accepted = await run(
      "add_connection",
      {
        provider_id: AI_CLI_INFO[values.cli].providerId,
        connection: {
          id: values.id,
          label: values.label,
          auth_ref: `cli:${values.id}`,
          scope: ["internal"],
          priority: 100,
          concurrency: 1,
          plan: values.planName
            ? { name: values.planName, price_usd: values.planPrice }
            : { price_usd: values.planPrice },
          meta: { cli: values.cli, models },
          units: {},
        },
      },
      {
        key: `add:${values.id}`,
        label: `Add AI account ${values.id}`,
        subject: values.label,
        onRejected: (reason) => setFormError(reason),
      },
    );
    if (accepted) {
      onOpenChange(false);
      form.reset();
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setFormError(null);
      }}
    >
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add an AI account</DialogTitle>
          <DialogDescription>
            Each account runs its own unmodified CLI in its own config folder. The Farm creates the account, then you
            sign in once on the PC.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={form.handleSubmit(onSubmit)} noValidate className="grid gap-4" aria-label="Add AI account">
          {formError ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}

          <Field>
            <FieldLabel htmlFor={idFor("cli")}>CLI type</FieldLabel>
            <NativeSelect
              id={idFor("cli")}
              className="w-full"
              {...form.register("cli", {
                onChange: (event) =>
                  form.setValue("models", AI_CLI_INFO[event.target.value as AiCli].models.join(", "), {
                    shouldValidate: true,
                  }),
              })}
            >
              {AI_CLIS.map((value) => (
                <NativeSelectOption key={value} value={value}>
                  {AI_CLI_INFO[value].label} ({value})
                </NativeSelectOption>
              ))}
            </NativeSelect>
            <FieldDescription>Joins the {AI_CLI_INFO[cli].providerId} pool.</FieldDescription>
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field data-invalid={Boolean(errors.id)}>
              <FieldLabel htmlFor={idFor("id")}>Account id</FieldLabel>
              <Input
                id={idFor("id")}
                autoComplete="off"
                spellCheck={false}
                placeholder={`${AI_CLI_INFO[cli].providerId}-04`}
                aria-invalid={Boolean(errors.id)}
                {...form.register("id")}
              />
              {errors.id ? <FieldError>{errors.id.message}</FieldError> : null}
            </Field>
            <Field data-invalid={Boolean(errors.label)}>
              <FieldLabel htmlFor={idFor("label")}>Label</FieldLabel>
              <Input
                id={idFor("label")}
                autoComplete="off"
                aria-invalid={Boolean(errors.label)}
                {...form.register("label")}
              />
              {errors.label ? <FieldError>{errors.label.message}</FieldError> : null}
            </Field>
          </div>

          <Field data-invalid={Boolean(errors.models)}>
            <FieldLabel htmlFor={idFor("models")}>Models</FieldLabel>
            <Input
              id={idFor("models")}
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
              aria-invalid={Boolean(errors.models)}
              {...form.register("models")}
            />
            {errors.models ? (
              <FieldError>{errors.models.message}</FieldError>
            ) : (
              <FieldDescription>
                Comma-separated. A task for a model only goes to accounts that list it.
              </FieldDescription>
            )}
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field data-invalid={Boolean(errors.planName)}>
              <FieldLabel htmlFor={idFor("plan")}>Plan (optional)</FieldLabel>
              <Input
                id={idFor("plan")}
                autoComplete="off"
                placeholder="Max 5x"
                aria-invalid={Boolean(errors.planName)}
                {...form.register("planName")}
              />
            </Field>
            <Field data-invalid={Boolean(errors.planPrice)}>
              <FieldLabel htmlFor={idFor("price")}>Price / month (USD)</FieldLabel>
              <Input
                id={idFor("price")}
                type="number"
                inputMode="decimal"
                step="0.01"
                min={0}
                aria-invalid={Boolean(errors.planPrice)}
                {...form.register("planPrice", { valueAsNumber: true })}
              />
              {errors.planPrice ? <FieldError>{errors.planPrice.message}</FieldError> : null}
            </Field>
          </div>

          <div className="rounded-lg border bg-muted/40 p-3 text-xs">
            <p className="mb-1.5 text-muted-foreground">After it is added, sign in once on the Farm PC:</p>
            <CopyCommand command={`farm ai login ${id || `${AI_CLI_INFO[cli].providerId}-04`}`} />
            <p className="mt-2 text-muted-foreground">
              Usage limits are detected when the CLI reports them; no key is stored.
            </p>
          </div>

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? <Spinner data-icon="inline-start" /> : null}
              Queue account
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
