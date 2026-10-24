import { redirect } from "next/navigation";

/** Billing moved into Usage & Cost. */
export default function BillingPage() {
  redirect("/usage");
}
