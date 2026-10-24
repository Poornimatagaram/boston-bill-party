"use client";

/**
 * "Read aloud" control used across the Results screen and the Voice Q&A answer.
 *
 * It speaks the provided text with `window.speechSynthesis`, choosing a voice
 * whose language matches the session's selected language and falling back to
 * the browser default voice when no matching voice is available.
 *
 * Mutual exclusion: all buttons share a single speaker via {@link useReadAloud}
 * (the `ReadAloudProvider` subtree, or a self-contained controller when used on
 * its own). Only one item speaks at a time — starting another cancels the
 * current one — and the active button shows "⏹" so the user can stop it.
 *
 * Two shapes:
 *  - default: a labelled button ("🔊 Read aloud" / "⏹ Stop").
 *  - `iconOnly`: a compact square 🔊/⏹ button for per-item triggers (next to a
 *    Summary bullet, a Detected-issue card, or the appeal letter).
 *
 * SSR safety: browser globals are only touched inside effects / event handlers.
 */

import { useId } from "react";
import type { SupportedLanguage } from "@/lib/api";
import { useReadAloud, useSpeechSynthesisSupported } from "./readAloud";

export default function ReadAloudButton({
  text,
  language,
  label = "Read aloud",
  id,
  iconOnly = false,
  className,
}: {
  /** The text to read aloud. */
  text: string;
  /** Session language; selects the voice (falls back to default voice). */
  language: SupportedLanguage;
  /** Accessible label for the speak action (defaults to "Read aloud"). */
  label?: string;
  /** Stable id for mutual-exclusion; auto-generated when omitted. */
  id?: string;
  /** Render a compact icon-only button (for per-item triggers). */
  iconOnly?: boolean;
  /** Optional extra classes for the button. */
  className?: string;
}) {
  const supported = useSpeechSynthesisSupported();
  const generatedId = useId();
  const triggerId = id ?? generatedId;
  const { activeId, speak, stop } = useReadAloud();

  const speaking = activeId === triggerId;

  if (!supported || !text.trim()) return null;

  const onClick = () =>
    speaking ? stop() : speak(triggerId, text, language);

  if (iconOnly) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-pressed={speaking}
        aria-label={speaking ? "Stop reading" : `Read aloud: ${label}`}
        title={speaking ? "Stop" : "Read aloud"}
        className={
          className ??
          "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-zinc-300 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
        }
      >
        {speaking ? "⏹" : "🔊"}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={speaking}
      className={
        className ??
        "inline-flex w-fit items-center justify-center gap-2 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-800 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
      }
    >
      {speaking ? `⏹ Stop` : `🔊 ${label}`}
    </button>
  );
}
