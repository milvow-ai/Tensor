import { notFound } from "next/navigation";
import { GalleryView } from "./gallery-view";

export const dynamic = "force-dynamic";

export default function GalleryPage() {
  // In production builds (unless running under E2E test harness), return 404
  if (process.env.NODE_ENV === "production" && !process.env.FARM_E2E) {
    notFound();
  }

  return <GalleryView />;
}
