"use client";

/**
 * "Show translation" toggle for the English appeal letter.
 *
 * Only rendered when the session language isn't English. On first open it asks
 * the backend (`POST /sessions/{id}/translate-letter`) for a read-only
 * translation of the CURRENT letter text and caches it. If the letter is edited
 * afterwards, the panel says the translation is out of date and offers to
 * update it. The English letter is always what gets confirmed and sent.
 */

import { useCallback, useId, useState } from "react";
import { ChevronDown, Info, Languages, LoaderCircle, RotateCcw } from "lucide-react";
import {
  ApiError,
  SUPPORTED_LANGUAGES,
  translateLetter,
  type SupportedLanguage,
} from "@/lib/api";
import { useT } from "@/i18n/uiLanguage";
import ReadAloudButton from "./ReadAloudButton";

type TranslationState =
  | { kind: "idle" }
  | { kind: "loading"; source: string }
  | { kind: "ready"; source: string; text: string }
  | { kind: "error"; message: string | null };

export default function LetterTranslation({
  sessionId,
  letter,
  language,
}: {
  sessionId: string;
  /** The current (English) letter text. */
  letter: string;
  /** The session language to translate into. */
  language: SupportedLanguage;
}) {
  const t = useT();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<TranslationState>({ kind: "idle" });

  const languageLabel =
    SUPPORTED_LANGUAGES.find((l) => l.value === language)?.label ?? language;

  const request = useCallback(
    async (source: string) => {
      setState({ kind: "loading", source });
      try {
        const { translation } = await translateLetter(sessionId, source);
        setState({ kind: "ready", source, text: translation });
      } catch (err) {
        setState({
          kind: "error",
          message: err instanceof ApiError ? err.message : null,
        });
      }
    },
    [sessionId],
  );

  const toggle = () => {
    const next = !open;
    setOpen(next);
    const upToDate = state.kind === "ready" && state.source === letter;
    if (next && !upToDate && state.kind !== "loading" && letter.trim()) {
      void request(letter);
    }
  };

  const outdated = state.kind === "ready" && state.source !== letter;

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={panelId}
        className="inline-flex w-fit items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-semibold text-teal-700 transition-colors hover:bg-teal-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
      >
        <Languages className="h-4 w-4" aria-hidden="true" />
        {open ? t.translation.hide : t.translation.show}
        <ChevronDown
          className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>

      {open ? (
        <section
          id={panelId}
          aria-label={t.translation.regionLabel}
          className="flex flex-col gap-3 rounded-2xl border border-teal-200 bg-teal-50/50 p-4"
        >
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold text-navy-900">
              {t.translation.heading(languageLabel)}
            </p>
            {state.kind === "ready" ? (
              <ReadAloudButton
                id="appeal-letter-translation"
                text={state.text}
                language={language}
                label={t.readAloud.labels.translation}
                iconOnly
              />
            ) : null}
          </div>

          <p className="flex items-start gap-2 rounded-xl bg-white px-3 py-2 text-sm font-medium text-navy-800 shadow-sm">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-coral-500" aria-hidden="true" />
            {t.translation.note}
          </p>

          <div aria-live="polite">
            {state.kind === "loading" ? (
              <p className="flex items-center gap-2 text-sm text-navy-600">
                <LoaderCircle
                  className="h-4 w-4 animate-spin text-teal-500"
                  aria-hidden="true"
                />
                {t.translation.loading}
              </p>
            ) : state.kind === "error" ? (
              <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-coral-800">
                <span>{state.message ?? t.translation.failed}</span>
                <button
                  type="button"
                  onClick={() => void request(letter)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 font-semibold text-navy-800 shadow-sm hover:bg-navy-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                >
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                  {t.translation.retry}
                </button>
              </div>
            ) : state.kind === "ready" ? (
              <div className="flex flex-col gap-2">
                {outdated ? (
                  <div className="flex flex-wrap items-center gap-3 text-xs text-amber-800">
                    <span>{t.translation.outdated}</span>
                    <button
                      type="button"
                      onClick={() => void request(letter)}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1 font-semibold text-navy-800 shadow-sm hover:bg-navy-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400"
                    >
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                      {t.translation.update}
                    </button>
                  </div>
                ) : null}
                {/* Read-only: plain text, not an editable field. */}
                <div
                  lang={language}
                  className="max-h-80 overflow-y-auto rounded-xl border border-navy-100 bg-white px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap text-navy-800"
                >
                  {state.text}
                </div>
              </div>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
