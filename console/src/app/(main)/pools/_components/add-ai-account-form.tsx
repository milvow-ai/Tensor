"use client";

import { useId, useState } from "react";

import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";

import { useSharedCommands } from "@/components/farm/commands-provider";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { AI_CLI_INFO, AI_CLIS, AI_EFFORTS, type AiCli } from "@/lib/farm/command-meta";
import {
  type AddAiAccountFormValues,
  addAiAccountFormSchema,
  buildAddConnectionPayload,
  buildSelectionDefaults,
  parseModels,
} from "@/lib/farm/commands";

import { SignInCard } from "./sign-in-card";

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
  const [addedAccountId, setAddedAccountId] = useState<string | null>(null);
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
      models: "",
      defaultsEnabled: false,
      defaultModel: "",
      defaultEffort: "",
      planName: "",
      planPrice: 0,
    },
  });
  const { errors, isSubmitting } = form.formState;
  const cli = form.watch("cli");
  const _id = form.watch("id");
  const defaultsEnabled = form.watch("defaultsEnabled");
  const defaultModel = form.watch("defaultModel");
  const defaultEffort = form.watch("defaultEffort");
  const modelOptions = [...new Set(parseModels(form.watch("models")))];
  const efforts = AI_EFFORTS[AI_CLI_INFO[cli].providerId];

  async function onSubmit(values: AddAiAccountFormValues) {
    setFormError(null);
    const providerId = AI_CLI_INFO[values.cli].providerId;
    const selectionDefaults = buildSelectionDefaults(providerId, {
      enabled: values.defaultsEnabled,
      model: values.defaultModel,
      effort: values.defaultEffort,
    });
    const accepted = await run(
      "add_connection",
      buildAddConnectionPayload({
        provider_id: providerId,
        id: values.id,
        label: values.label,
        auth_ref: `cli:${values.id}`,
        scope: ["internal"],
        priority: 100,
        concurrency: 1,
        // The Farm's PlanSpec requires a name; a nameless plan made the whole registry fail to load.
        plan: { name: values.planName || "Default", price_usd: values.planPrice },
        meta: { cli: values.cli, models: parseModels(values.models), selection_defaults: selectionDefaults },
        units: {},
      }),
      {
        key: `add:${values.id}`,
        label: `Add AI account ${values.id}`,
        subject: values.label,
        onRejected: (reason) => setFormError(reason),
      },
    );
    if (accepted) {
      setAddedAccountId(values.id);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setFormError(null);
          setAddedAccountId(null);
          form.reset();
        }
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
        {addedAccountId ? (
          <div className="space-y-4 py-2">
            <SignInCard
              connectionId={addedAccountId}
              providerId={AI_CLI_INFO[form.getValues("cli")].providerId}
              onDone={() => {
                onOpenChange(false);
                setAddedAccountId(null);
                form.reset();
              }}
              onCancelled={() => {
                onOpenChange(false);
                setAddedAccountId(null);
                form.reset();
              }}
            />
          </div>
        ) : (
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
                  onChange: () => form.setValue("defaultEffort", "", { shouldValidate: true }),
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

            <div className="flex items-center gap-2">
              <Checkbox
                id={idFor("defaults")}
                checked={defaultsEnabled}
                onCheckedChange={(checked) =>
                  form.setValue("defaultsEnabled", checked === true, { shouldValidate: true })
                }
              />
              <FieldLabel htmlFor={idFor("defaults")}>Use account defaults when a request names none</FieldLabel>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field data-invalid={Boolean(errors.defaultModel)}>
                <FieldLabel htmlFor={idFor("default-model")}>Default model</FieldLabel>
                <NativeSelect
                  id={idFor("default-model")}
                  className="w-full"
                  disabled={!defaultsEnabled || modelOptions.length === 0}
                  aria-invalid={Boolean(errors.defaultModel)}
                  value={modelOptions.includes(defaultModel) ? defaultModel : ""}
                  onChange={(event) => form.setValue("defaultModel", event.target.value, { shouldValidate: true })}
                >
                  <NativeSelectOption value="">Choose a model</NativeSelectOption>
                  {modelOptions.map((model) => (
                    <NativeSelectOption key={model} value={model}>
                      {model}
                    </NativeSelectOption>
                  ))}
                </NativeSelect>
                {errors.defaultModel ? <FieldError>{errors.defaultModel.message}</FieldError> : null}
              </Field>
              {efforts ? (
                <Field data-invalid={Boolean(errors.defaultEffort)}>
                  <FieldLabel htmlFor={idFor("default-effort")}>Default effort</FieldLabel>
                  <NativeSelect
                    id={idFor("default-effort")}
                    className="w-full"
                    disabled={!defaultsEnabled}
                    aria-invalid={Boolean(errors.defaultEffort)}
                    value={defaultEffort}
                    onChange={(event) => form.setValue("defaultEffort", event.target.value, { shouldValidate: true })}
                  >
                    <NativeSelectOption value="">Choose an effort</NativeSelectOption>
                    {efforts.map((effort) => (
                      <NativeSelectOption key={effort} value={effort}>
                        {effort}
                      </NativeSelectOption>
                    ))}
                  </NativeSelect>
                  {errors.defaultEffort ? <FieldError>{errors.defaultEffort.message}</FieldError> : null}
                </Field>
              ) : null}
            </div>

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

            <p className="text-muted-foreground text-xs">
              Usage limits are detected when the CLI reports them; no key is stored.
            </p>

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
        )}
      </DialogContent>
    </Dialog>
  );
}
