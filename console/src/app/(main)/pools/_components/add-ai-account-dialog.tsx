"use client";

import { useRef, useState } from "react";

import dynamic from "next/dynamic";

import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { AiCli } from "@/lib/farm/command-meta";

// The form (react-hook-form and zod) is only needed once someone opens it, so it is a separate chunk.
const loadForm = () => import("./add-ai-account-form").then((module) => module.AddAiAccountForm);
const AddAiAccountForm = dynamic(loadForm, { ssr: false });

/** "Add AI account" button; the form dialog loads on first use. */
export function AddAiAccountDialog(props: { defaultCli?: AiCli; suggestedId?: string }) {
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
        Add AI account
      </Button>
      {mounted ? (
        <AddAiAccountForm
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
