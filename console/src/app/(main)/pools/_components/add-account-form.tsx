"use client";

import { useId, useState } from "react";

import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, Trash2 } from "lucide-react";
import { useFieldArray, useForm } from "react-hook-form";

import { useSharedCommands } from "@/components/farm/commands-provider";
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
import { CHARGED_ON, PERIOD_LABELS, PERIODS } from "@/lib/farm/command-meta";
import {
  type AddAccountFormValues,
  addAccountFormSchema,
  buildAddConnectionPayload,
  type UnitFormValues,
} from "@/lib/farm/commands";

const EMPTY_UNIT: UnitFormValues = { unit: "", limit: null, period: "month", anchor: null, chargedOn: "attempt" };

/** Empty number inputs mean "not set" (null), anything else is read as a number. */
const optionalNumber = (value: unknown): number | null =>
  value === "" || value === null || value === undefined ? null : Number(value);

export function AddAccountForm({
  providerId,
  providerName,
  suggestedId,
  nextPriority,
  open,
  onOpenChange,
}: {
  providerId: string;
  providerName: string;
  suggestedId: string;
  nextPriority: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const { run } = useSharedCommands();
  const uid = useId();

  const form = useForm<AddAccountFormValues>({
    resolver: zodResolver(addAccountFormSchema),
    mode: "onChange",
    defaultValues: {
      id: suggestedId,
      label: "",
      authEnv: "",
      planName: "",
      planPrice: 0,
      billingDay: 1,
      priority: nextPriority,
      concurrency: 1,
      units: [{ ...EMPTY_UNIT, unit: "credits" }],
    },
  });
  const units = useFieldArray({ control: form.control, name: "units" });
  const { errors, isSubmitting } = form.formState;

  async function onSubmit(values: AddAccountFormValues) {
    setFormError(null);
    const accepted = await run(
      "add_connection",
      buildAddConnectionPayload({
        provider_id: providerId,
        id: values.id,
        label: values.label,
        auth_ref: `env:${values.authEnv}`,
        scope: ["internal"],
        priority: values.priority,
        concurrency: values.concurrency,
        plan: { name: values.planName, price_usd: values.planPrice, billing_day: values.billingDay },
        meta: {},
        units: Object.fromEntries(
          values.units.map((unit) => [
            unit.unit,
            {
              limit: unit.limit,
              period: unit.period,
              anchor: unit.period === "month" ? (unit.anchor ?? values.billingDay) : null,
              charged_on: unit.chargedOn,
              unit_cost_usd: 0,
              estimate_per_call: 1,
            },
          ]),
        ),
      }),
      {
        key: `add:${values.id}`,
        label: `Add account ${values.id}`,
        subject: values.label,
        onRejected: (reason) => setFormError(reason),
      },
    );
    if (accepted) {
      onOpenChange(false);
      form.reset();
    }
  }

  const idFor = (name: string) => `${uid}-${name}`;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setFormError(null);
      }}
    >
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add a {providerName} account</DialogTitle>
          <DialogDescription>
            The account is queued for the Farm, which validates it and adds it to the pool. Nothing secret is entered
            here.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          noValidate
          className="grid gap-4"
          aria-label={`Add ${providerName} account`}
        >
          {formError ? (
            <Alert variant="destructive" role="alert">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field data-invalid={Boolean(errors.id)}>
              <FieldLabel htmlFor={idFor("id")}>Account id</FieldLabel>
              <Input
                id={idFor("id")}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={Boolean(errors.id)}
                {...form.register("id")}
              />
              {errors.id ? (
                <FieldError>{errors.id.message}</FieldError>
              ) : (
                <FieldDescription>Short and permanent, e.g. {suggestedId}.</FieldDescription>
              )}
            </Field>
            <Field data-invalid={Boolean(errors.label)}>
              <FieldLabel htmlFor={idFor("label")}>Label</FieldLabel>
              <Input
                id={idFor("label")}
                autoComplete="off"
                aria-invalid={Boolean(errors.label)}
                {...form.register("label")}
              />
              {errors.label ? (
                <FieldError>{errors.label.message}</FieldError>
              ) : (
                <FieldDescription>How you recognise it in tables.</FieldDescription>
              )}
            </Field>
          </div>

          <Field data-invalid={Boolean(errors.authEnv)}>
            <FieldLabel htmlFor={idFor("auth")}>Auth reference (env-var name)</FieldLabel>
            <Input
              id={idFor("auth")}
              autoComplete="off"
              spellCheck={false}
              placeholder={`${providerId.toUpperCase()}_KEY_08`}
              className="font-mono"
              aria-invalid={Boolean(errors.authEnv)}
              aria-describedby={idFor("auth-help")}
              {...form.register("authEnv")}
            />
            {errors.authEnv ? <FieldError>{errors.authEnv.message}</FieldError> : null}
            <FieldDescription id={idFor("auth-help")}>
              Only the variable's name. The secret stays in the PC's .env: run{" "}
              <code className="rounded bg-muted px-1 font-mono text-xs">farm set-secret &lt;NAME&gt;</code> there.
            </FieldDescription>
          </Field>

          <div className="grid gap-4 sm:grid-cols-4">
            <Field data-invalid={Boolean(errors.planName)} className="sm:col-span-2">
              <FieldLabel htmlFor={idFor("plan")}>Plan name</FieldLabel>
              <Input
                id={idFor("plan")}
                autoComplete="off"
                placeholder="Launch"
                aria-invalid={Boolean(errors.planName)}
                {...form.register("planName")}
              />
              {errors.planName ? <FieldError>{errors.planName.message}</FieldError> : null}
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
            <Field data-invalid={Boolean(errors.billingDay)}>
              <FieldLabel htmlFor={idFor("day")}>Billing day</FieldLabel>
              <Input
                id={idFor("day")}
                type="number"
                inputMode="numeric"
                min={1}
                max={31}
                aria-invalid={Boolean(errors.billingDay)}
                {...form.register("billingDay", { valueAsNumber: true })}
              />
              {errors.billingDay ? <FieldError>{errors.billingDay.message}</FieldError> : null}
            </Field>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field data-invalid={Boolean(errors.priority)}>
              <FieldLabel htmlFor={idFor("priority")}>Priority</FieldLabel>
              <Input
                id={idFor("priority")}
                type="number"
                inputMode="numeric"
                min={1}
                aria-invalid={Boolean(errors.priority)}
                {...form.register("priority", { valueAsNumber: true })}
              />
              {errors.priority ? (
                <FieldError>{errors.priority.message}</FieldError>
              ) : (
                <FieldDescription>Lower numbers are tried first.</FieldDescription>
              )}
            </Field>
            <Field data-invalid={Boolean(errors.concurrency)}>
              <FieldLabel htmlFor={idFor("concurrency")}>Concurrency</FieldLabel>
              <Input
                id={idFor("concurrency")}
                type="number"
                inputMode="numeric"
                min={1}
                aria-invalid={Boolean(errors.concurrency)}
                {...form.register("concurrency", { valueAsNumber: true })}
              />
              {errors.concurrency ? (
                <FieldError>{errors.concurrency.message}</FieldError>
              ) : (
                <FieldDescription>Calls in flight at once.</FieldDescription>
              )}
            </Field>
          </div>

          <fieldset className="grid gap-3">
            <legend className="font-medium text-sm">Consumption units</legend>
            <p className="text-muted-foreground text-xs">
              What this account spends per call and when it resets. Leave the limit empty for unlimited.
            </p>
            {units.fields.map((unit, index) => {
              const unitErrors = errors.units?.[index];
              const period = form.watch(`units.${index}.period`);
              return (
                <div
                  key={unit.id}
                  className="grid gap-3 rounded-lg border p-3 sm:grid-cols-[1.2fr_1fr_1.1fr_0.7fr_1fr_auto] sm:items-start"
                >
                  <Field data-invalid={Boolean(unitErrors?.unit)}>
                    <FieldLabel htmlFor={idFor(`u${index}-name`)}>Unit</FieldLabel>
                    <Input
                      id={idFor(`u${index}-name`)}
                      autoComplete="off"
                      placeholder="credits"
                      aria-invalid={Boolean(unitErrors?.unit)}
                      {...form.register(`units.${index}.unit`)}
                    />
                    {unitErrors?.unit ? <FieldError>{unitErrors.unit.message}</FieldError> : null}
                  </Field>
                  <Field data-invalid={Boolean(unitErrors?.limit)}>
                    <FieldLabel htmlFor={idFor(`u${index}-limit`)}>Limit</FieldLabel>
                    <Input
                      id={idFor(`u${index}-limit`)}
                      type="number"
                      inputMode="numeric"
                      min={0}
                      placeholder="unlimited"
                      aria-invalid={Boolean(unitErrors?.limit)}
                      {...form.register(`units.${index}.limit`, { setValueAs: optionalNumber })}
                    />
                    {unitErrors?.limit ? <FieldError>{unitErrors.limit.message}</FieldError> : null}
                  </Field>
                  <Field>
                    <FieldLabel htmlFor={idFor(`u${index}-period`)}>Resets</FieldLabel>
                    <NativeSelect
                      id={idFor(`u${index}-period`)}
                      className="w-full"
                      {...form.register(`units.${index}.period`)}
                    >
                      {PERIODS.map((value) => (
                        <NativeSelectOption key={value} value={value}>
                          {PERIOD_LABELS[value]}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                  </Field>
                  <Field data-invalid={Boolean(unitErrors?.anchor)}>
                    <FieldLabel htmlFor={idFor(`u${index}-anchor`)}>Anchor day</FieldLabel>
                    <Input
                      id={idFor(`u${index}-anchor`)}
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={31}
                      placeholder="billing"
                      disabled={period !== "month"}
                      aria-invalid={Boolean(unitErrors?.anchor)}
                      {...form.register(`units.${index}.anchor`, { setValueAs: optionalNumber })}
                    />
                    {unitErrors?.anchor ? <FieldError>{unitErrors.anchor.message}</FieldError> : null}
                  </Field>
                  <Field>
                    <FieldLabel htmlFor={idFor(`u${index}-charged`)}>Charged on</FieldLabel>
                    <NativeSelect
                      id={idFor(`u${index}-charged`)}
                      className="w-full"
                      {...form.register(`units.${index}.chargedOn`)}
                    >
                      {CHARGED_ON.map((value) => (
                        <NativeSelectOption key={value} value={value}>
                          {value}
                        </NativeSelectOption>
                      ))}
                    </NativeSelect>
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="self-end text-muted-foreground"
                    disabled={units.fields.length === 1}
                    onClick={() => units.remove(index)}
                    aria-label={`Remove unit ${index + 1}`}
                  >
                    <Trash2 aria-hidden="true" />
                  </Button>
                </div>
              );
            })}
            {(errors.units?.root ?? errors.units?.message) ? (
              <FieldError>{errors.units?.root?.message ?? errors.units?.message}</FieldError>
            ) : null}
            <div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={units.fields.length >= 6}
                onClick={() => units.append({ ...EMPTY_UNIT })}
              >
                <Plus data-icon="inline-start" aria-hidden="true" />
                Add unit
              </Button>
            </div>
          </fieldset>

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
