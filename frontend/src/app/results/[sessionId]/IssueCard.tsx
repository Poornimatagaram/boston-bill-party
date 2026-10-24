"use client";

/**
 * Compact, color-coded card for one retained Finding.
 *
 * Each FindingType gets its own icon and color (left accent, solid icon chip,
 * and confidence bar). Long descriptions (typically rights issues, which quote
 * the reference passage) are clamped to two lines with a "Show details" toggle.
 */

import { useId, useState } from "react";
import {
  Calculator,
  ChevronDown,
  Files,
  ScanSearch,
  Scale,
  type LucideIcon,
} from "lucide-react";
import type { Finding, FindingType } from "@/lib/api";
import type { Dictionary } from "@/i18n/dictionary";
import { useT } from "@/i18n/uiLanguage";
import ReadAloudButton from "./ReadAloudButton";

/** Descriptions longer than this collapse behind a details toggle. */
const LONG_DESCRIPTION_CHARS = 140;

interface TypeStyle {
  icon: LucideIcon;
  accent: string;
  chip: string;
  bar: string;
}

/** Visual style per FindingType. Chips are solid so each type reads at a glance. */
const FINDING_TYPE_STYLE: Record<FindingType, TypeStyle> = {
  math_error: {
    icon: Calculator,
    accent: "border-l-amber-400",
    chip: "bg-amber-500 text-white",
    bar: "bg-amber-400",
  },
  duplicate_charge: {
    icon: Files,
    accent: "border-l-coral-500",
    chip: "bg-coral-500 text-white",
    bar: "bg-coral-500",
  },
  suspicious_code: {
    icon: ScanSearch,
    accent: "border-l-violet-400",
    chip: "bg-violet-500 text-white",
    bar: "bg-violet-400",
  },
  rights_issue: {
    icon: Scale,
    accent: "border-l-teal-400",
    chip: "bg-teal-500 text-white",
    bar: "bg-teal-500",
  },
};

const FALLBACK_STYLE: TypeStyle = {
  icon: ScanSearch,
  accent: "border-l-navy-300",
  chip: "bg-navy-500 text-white",
  bar: "bg-navy-400",
};

/** Style plus the translated label for a FindingType. */
export function findingMeta(type: FindingType, t: Dictionary) {
  return {
    ...(FINDING_TYPE_STYLE[type] ?? FALLBACK_STYLE),
    label: t.issues.types[type] ?? t.issues.fallbackType,
  };
}

/** Parse a confidence string in [0,1] into a clamped percentage (0–100). */
function confidencePct(confidence: string): number {
  const n = Number.parseFloat(confidence);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}

export default function IssueCard({ finding }: { finding: Finding }) {
  const t = useT();
  const descriptionId = useId();
  const [expanded, setExpanded] = useState(false);

  const meta = findingMeta(finding.type, t);
  const Icon = meta.icon;
  const pct = confidencePct(finding.confidence);
  const isLong = finding.description.length > LONG_DESCRIPTION_CHARS;

  return (
    <li
      className={`rounded-2xl border border-l-4 border-navy-100 bg-white p-4 shadow-card ${meta.accent}`}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden="true"
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl shadow-sm ${meta.chip}`}
        >
          <Icon className="h-4 w-4" />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 flex-col gap-1">
              <p className="text-sm font-semibold text-navy-900">{meta.label}</p>
              {/* Compact confidence bar. */}
              <div className="flex items-center gap-2">
                <div
                  role="progressbar"
                  aria-valuenow={pct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={t.issues.confidenceAria(pct)}
                  className="h-1.5 w-24 overflow-hidden rounded-full bg-navy-100 sm:w-32"
                >
                  <div
                    className={`h-full rounded-full ${meta.bar}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="font-mono text-[11px] text-navy-500 tabular-nums">
                  {t.issues.confidence(pct)}
                </span>
              </div>
            </div>
            {/* Descriptions come from the backend in English, so read them
                with an English voice. */}
            <ReadAloudButton
              id={`finding-${finding.id}`}
              text={finding.description}
              language="en"
              label={t.readAloud.labels.issue}
              iconOnly
            />
          </div>

          <p
            id={descriptionId}
            lang="en"
            className={`mt-2 text-sm leading-relaxed text-navy-700 ${
              isLong && !expanded ? "line-clamp-2" : ""
            }`}
          >
            {finding.description}
          </p>

          {isLong ? (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              aria-controls={descriptionId}
              className="mt-1 inline-flex items-center gap-1 rounded-md text-xs font-semibold text-teal-700 hover:text-teal-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
            >
              {expanded ? t.issues.hideDetails : t.issues.showDetails}
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
                aria-hidden="true"
              />
            </button>
          ) : null}
        </div>
      </div>
    </li>
  );
}
