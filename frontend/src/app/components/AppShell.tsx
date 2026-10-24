import type { ReactNode } from "react";
import BrandHeader from "./BrandHeader";

/**
 * Page shell shared by every screen: the branded header above a centered,
 * mobile-friendly content column with an optional page title + subtitle. Keeps
 * spacing and max-width consistent so the three screens feel like one app.
 */
export default function AppShell({
  title,
  subtitle,
  children,
}: {
  title?: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col bg-canvas">
      <BrandHeader />
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 py-8 sm:px-6 sm:py-12">
        {title ? (
          <div className="flex flex-col gap-1">
            <h1 className="text-xl font-semibold tracking-tight text-navy-900 sm:text-2xl dark:text-white">
              {title}
            </h1>
            {subtitle ? (
              <p className="text-sm text-navy-500 sm:text-base dark:text-navy-200">
                {subtitle}
              </p>
            ) : null}
          </div>
        ) : null}
        {children}
      </main>
    </div>
  );
}
