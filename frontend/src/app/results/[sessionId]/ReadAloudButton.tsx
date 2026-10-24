"use client";

/**
 * Small ghost "Read aloud" control used across the Results screen and the
 * chat assistant.
 *
 * It speaks the provided text with `window.speechSynthesis`, choosing a voice
 * whose language matches the session's selected language and falling back to
 * the browser default voice when no matching voice is available.
 *
 * Mutual exclusion: all buttons share a single speaker via {@link useReadAloud}
 * (the `ReadAloudProvider` subtree, or a self-contained controller when used on
 * its own). Only one item speaks at a time — starting another cancels the
 * current one — and the active button switches from Volume2 to Square so the
 * user can stop it.
 *
 * Two shapes:
 *  - default: a labelled ghost button ("Read aloud" / "Stop").
 *  - `iconOnly`: a compact square ghost button for per-item triggers.
 *
 * SSR safety: browser globals are only touched inside effects / event handlers.
 */

import { useId } from "react";
import { Square, Volume2 } from "lucide-react";
import type { SupportedLanguage } from "@/lib/api";
import { useT } from "@/i18n/uiLanguage";
import { useReadAloud, useSpeechSynthesisSupported } from "./readAloud";

export default function ReadAloudButton({
  text,
  language,
  label,
  id,
  iconOnly = false,
  className,
}: {
  /** The text to read aloud. */
  text: string;
  /** Session language; selects the voice (falls back to default voice). */
  language: SupportedLanguage;
  /**
   * Labelled button: its visible text (defaults to "Read aloud").
   * Icon button: what is being read, used in its aria-label.
   */
  label?: string;
  /** Stable id for mutual-exclusion; auto-generated when omitted. */
  id?: string;
  /** Render a compact icon-only button (for per-item triggers). */
  iconOnly?: boolean;
  /** Optional extra classes appended to the button. */
  className?: string;
}) {
  const t = useT();
  const supported = useSpeechSynthesisSupported();
  const generatedId = useId();
  const triggerId = id ?? generatedId;
  const { activeId, speak, stop } = useReadAloud();

  const speaking = activeId === triggerId;

  if (!supported || !text.trim()) return null;

  const onClick = () =>
    speaking ? stop() : speak(triggerId, text, language);

  const Icon = speaking ? Square : Volume2;
  const tone = speaking
    ? "bg-coral-50 text-coral-600 hover:bg-coral-100"
    : "text-navy-400 hover:bg-teal-50 hover:text-teal-700";

  if (iconOnly) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-pressed={speaking}
        aria-label={
          speaking
            ? t.readAloud.stopReading
            : label
              ? t.readAloud.item(label)
              : t.readAloud.readAloud
        }
        title={speaking ? t.readAloud.stop : t.readAloud.readAloud}
        className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${tone} ${className ?? ""}`}
      >
        <Icon
          className={speaking ? "h-3.5 w-3.5 fill-current" : "h-4 w-4"}
          aria-hidden="true"
        />
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={speaking}
      className={`inline-flex w-fit items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${
        speaking
          ? "bg-coral-50 text-coral-600 hover:bg-coral-100"
          : "text-teal-700 hover:bg-teal-50"
      } ${className ?? ""}`}
    >
      <Icon
        className={speaking ? "h-3.5 w-3.5 fill-current" : "h-4 w-4"}
        aria-hidden="true"
      />
      {speaking ? t.readAloud.stop : (label ?? t.readAloud.readAloud)}
    </button>
  );
}
