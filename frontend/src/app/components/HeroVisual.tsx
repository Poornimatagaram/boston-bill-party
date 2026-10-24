"use client";

/**
 * Hero visual for the home page.
 *
 * Shows `/images/hero.jpg` when the server found it in `public/` (`hasImage`),
 * layered over a gradient illustration. If the file is missing, or fails to
 * load at runtime, only the illustration is shown, so the hero never renders a
 * broken image.
 */

import Image from "next/image";
import { useState } from "react";
import { useT } from "@/i18n/uiLanguage";
import {
  CircleCheck,
  FileText,
  ScanSearch,
  Scale,
  TriangleAlert,
} from "lucide-react";

export default function HeroVisual({ hasImage }: { hasImage: boolean }) {
  const t = useT();
  const [failed, setFailed] = useState(false);
  const showImage = hasImage && !failed;

  return (
    <div className="relative aspect-[4/3] w-full overflow-hidden rounded-[2rem] shadow-card-lg ring-1 ring-white/60">
      <GradientIllustration
        duplicateLabel={t.hero.chipDuplicate}
        letterLabel={t.hero.chipLetter}
      />
      {showImage ? (
        <Image
          src="/images/hero.jpg"
          alt={t.hero.imageAlt}
          fill
          sizes="(min-width: 1024px) 560px, 100vw"
          loading="eager"
          fetchPriority="high"
          className="object-cover"
          onError={() => setFailed(true)}
        />
      ) : null}
    </div>
  );
}

/** Decorative gradient "bill under review" illustration (fallback visual). */
function GradientIllustration({
  duplicateLabel,
  letterLabel,
}: {
  duplicateLabel: string;
  letterLabel: string;
}) {
  return (
    <div
      aria-hidden="true"
      className="absolute inset-0 bg-linear-to-br from-navy-800 via-teal-600 to-coral-400"
    >
      {/* Soft glow blobs. */}
      <div className="absolute -top-16 -left-10 h-56 w-56 rounded-full bg-teal-300/40 blur-3xl" />
      <div className="absolute -right-12 -bottom-16 h-64 w-64 rounded-full bg-coral-300/50 blur-3xl" />

      {/* Mock bill card. */}
      <div className="absolute top-1/2 left-1/2 w-[62%] -translate-x-1/2 -translate-y-1/2 -rotate-3 rounded-2xl bg-white/95 p-4 shadow-card-lg sm:p-5">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-navy-100 text-navy-700">
            <FileText className="h-4 w-4" />
          </span>
          <div className="flex flex-col gap-1">
            <span className="h-2 w-24 rounded-full bg-navy-200" />
            <span className="h-2 w-16 rounded-full bg-navy-100" />
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-2">
          {[0, 1, 2, 3].map((row) => (
            <div key={row} className="flex items-center justify-between gap-3">
              <span
                className={`h-2 rounded-full ${row === 2 ? "w-28 bg-coral-300" : "w-32 bg-navy-100"}`}
              />
              <span
                className={`h-2 w-10 rounded-full ${row === 2 ? "bg-coral-400" : "bg-navy-200"}`}
              />
            </div>
          ))}
        </div>
        <div className="mt-4 h-px bg-navy-100" />
        <div className="mt-3 flex items-center justify-between">
          <span className="h-2.5 w-14 rounded-full bg-navy-300" />
          <span className="h-2.5 w-12 rounded-full bg-teal-400" />
        </div>
      </div>

      {/* Floating finding chips. */}
      <div className="absolute top-[12%] right-[6%] flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-xs font-semibold text-coral-700 shadow-card">
        <TriangleAlert className="h-4 w-4" />
        {duplicateLabel}
      </div>
      <div className="absolute bottom-[12%] left-[6%] flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-xs font-semibold text-teal-700 shadow-card">
        <CircleCheck className="h-4 w-4" />
        {letterLabel}
      </div>
      <div className="absolute top-[14%] left-[8%] flex h-11 w-11 items-center justify-center rounded-full bg-white text-navy-700 shadow-card">
        <ScanSearch className="h-5 w-5" />
      </div>
      <div className="absolute right-[8%] bottom-[16%] flex h-11 w-11 items-center justify-center rounded-full bg-white text-teal-600 shadow-card">
        <Scale className="h-5 w-5" />
      </div>
    </div>
  );
}
