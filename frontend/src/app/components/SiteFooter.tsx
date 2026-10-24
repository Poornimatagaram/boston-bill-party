"use client";

/**
 * Site footer: synthetic-data disclaimer, hackathon credit, and GitHub link.
 * (lucide 1.x ships no brand icons, so the GitHub link uses the Code icon.)
 */

import { Code, HeartPulse, ShieldCheck } from "lucide-react";
import { useT } from "@/i18n/uiLanguage";

const GITHUB_URL = "https://github.com/Poornimatagaram/boston-bill-party";

export default function SiteFooter() {
  const t = useT();

  return (
    <footer className="mt-auto bg-navy-950 text-navy-200">
      {/* Extra bottom padding keeps footer links clear of the results page's
          floating chat button. */}
      <div className="mx-auto grid max-w-6xl gap-8 px-4 pt-10 pb-24 sm:px-6 md:grid-cols-[1.4fr_1fr]">
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2 text-white">
            <span
              aria-hidden="true"
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-linear-to-br from-navy-600 via-teal-500 to-coral-400"
            >
              <HeartPulse className="h-4 w-4" />
            </span>
            <span className="font-semibold">Boston Bill Party</span>
          </div>
          <p className="flex max-w-xl items-start gap-2 text-sm leading-relaxed">
            <ShieldCheck
              className="mt-0.5 h-4 w-4 shrink-0 text-teal-300"
              aria-hidden="true"
            />
            <span>
              <span className="font-semibold text-white">
                {t.footer.syntheticStrong}
              </span>{" "}
              {t.footer.syntheticBody}
            </span>
          </p>
        </div>

        <div className="flex flex-col gap-3 md:items-end">
          <p className="text-sm">
            {t.footer.builtAt}{" "}
            <span className="font-semibold text-white">Hackachusetts 2026</span>
          </p>
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex w-fit items-center gap-2 rounded-lg border border-navy-700 px-3 py-2 text-sm font-medium text-white transition-colors hover:border-teal-400 hover:text-teal-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
          >
            <Code className="h-4 w-4" aria-hidden="true" />
            {t.footer.github}
            <span className="sr-only">{t.common.newTab}</span>
          </a>
        </div>
      </div>
    </footer>
  );
}
