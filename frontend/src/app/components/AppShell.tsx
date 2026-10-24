"use client";

import type { ReactNode } from "react";
import { useT } from "@/i18n/uiLanguage";

/**
 * Page container for the Progress and Results screens: a soft gradient title
 * band above a centered, mobile-friendly content column. The title/subtitle
 * come from the UI dictionary (`pages[page]`). The sticky header and footer
 * come from the root layout.
 */
export default function AppShell({
  page,
  children,
}: {
  page: "progress" | "results";
  children: ReactNode;
}) {
  const { title, subtitle } = useT().pages[page];

  return (
    <main className="relative flex flex-1 flex-col">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-72 bg-linear-to-br from-teal-50 via-white to-coral-50"
      />
      <div className="relative mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 pt-8 pb-28 sm:px-6 sm:pt-12">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold tracking-tight text-navy-900 sm:text-3xl">
            {title}
          </h1>
          <p className="text-sm text-navy-500 sm:text-base">{subtitle}</p>
        </div>
        {children}
      </div>
    </main>
  );
}
