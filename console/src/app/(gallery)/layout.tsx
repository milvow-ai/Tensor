import type { ReactNode } from "react";

import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import "@/styles/kit-tokens.css";

export const metadata = {
  title: "Component gallery · Harness Farm",
  description: "Every Harness Farm component in every state, light and dark.",
  robots: { index: false, follow: false },
};

export default function GalleryLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  return (
    <div
      className={`${GeistSans.variable} ${GeistMono.variable} kit-theme min-h-screen bg-[var(--kit-bg-canvas,#f4f5f7)] text-[var(--kit-text-primary,#111827)] antialiased transition-colors duration-200`}
    >
      {children}
    </div>
  );
}
