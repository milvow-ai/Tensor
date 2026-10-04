"use client";

import { useRef, useState } from "react";

import dynamic from "next/dynamic";

import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

// The form (react-hook-form and zod) is only needed once someone opens it, so it is a separate chunk.
const loadForm = () => import("./add-account-form").then((module) => module.AddAccountForm);
const AddAccountForm = dynamic(loadForm, { ssr: false });

/** "Add account" button for a tool pool; the form dialog loads on first use. */
export function AddAccountDialog(props: {
  providerId: string;
  providerName: string;
  suggestedId: string;
  nextPriority: number;
}) {
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  return (
    <>
      <Button
        ref={trigger}
        size="sm"
        onClick={() => {
          setMounted(true);
          setOpen(true);
        }}
        onPointerEnter={() => void loadForm()}
        onFocus={() => void loadForm()}
      >
        <Plus data-icon="inline-start" aria-hidden="true" />
        Add account
      </Button>
      {mounted ? (
        <AddAccountForm
          {...props}
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) trigger.current?.focus();
          }}
        />
      ) : null}
    </>
  );
}
