"use client";

/**
 * Shared "read aloud" controller for the Results screen.
 *
 * The screen has many read-aloud triggers (the Summary "read all" button, one
 * per Summary bullet, one per Detected-issue card, and one for the appeal
 * letter). Only ONE may speak at a time: starting any utterance cancels the
 * current one. To coordinate that across independent buttons, a single provider
 * owns the lone `window.speechSynthesis` queue and tracks which trigger id is
 * currently speaking; each button reads that shared state to decide whether it
 * should show "🔊 …" (idle) or "⏹ Stop" (it is the active speaker).
 *
 * Voice selection matches the session language with a graceful fallback to the
 * browser default voice (same behavior the previous standalone button had).
 *
 * SSR safety: browser globals are only touched inside effects / event handlers.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { SupportedLanguage } from "@/lib/api";

/** Map a SupportedLanguage to the BCP-47 tag used to pick a synthesis voice. */
const SPEECH_LANG: Record<SupportedLanguage, string> = {
  en: "en-US",
  es: "es-US",
  pt: "pt-BR",
  ht: "ht-HT",
};

export function hasSpeechSynthesis(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/** Feature-detect speechSynthesis, SSR-safe (unknown on the server). */
export function useSpeechSynthesisSupported(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => hasSpeechSynthesis(),
    () => false,
  );
}

/**
 * Pick the best available voice for `langTag` (e.g. "es-US"): an exact match
 * first, then any voice sharing the primary subtag (e.g. "es-*"), else null so
 * the browser uses its default voice.
 */
function pickVoice(langTag: string): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices();
  if (voices.length === 0) return null;
  const lower = langTag.toLowerCase();
  const primary = lower.split("-")[0];
  return (
    voices.find((v) => v.lang.toLowerCase() === lower) ??
    voices.find((v) => v.lang.toLowerCase().split("-")[0] === primary) ??
    null
  );
}

interface ReadAloudController {
  /** The id of the trigger currently speaking, or null when idle. */
  activeId: string | null;
  /** Speak `text` for `id`, cancelling any current utterance first. */
  speak: (id: string, text: string, language: SupportedLanguage) => void;
  /** Stop any current utterance. */
  stop: () => void;
}

const ReadAloudContext = createContext<ReadAloudController | null>(null);

/**
 * Provider that owns the single speechSynthesis queue. Wrap any subtree whose
 * read-aloud buttons must be mutually exclusive.
 */
export function ReadAloudProvider({ children }: { children: ReactNode }) {
  const [activeId, setActiveId] = useState<string | null>(null);
  // Guards late onend/onerror from a cancelled utterance clobbering a newer one.
  const tokenRef = useRef(0);

  const stop = useCallback(() => {
    tokenRef.current += 1;
    if (hasSpeechSynthesis()) window.speechSynthesis.cancel();
    setActiveId(null);
  }, []);

  const speak = useCallback(
    (id: string, text: string, language: SupportedLanguage) => {
      if (!hasSpeechSynthesis() || !text.trim()) return;
      // Cancel whatever is speaking (mutual exclusion) and claim a new token.
      tokenRef.current += 1;
      const myToken = tokenRef.current;
      try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        const langTag = SPEECH_LANG[language] ?? SPEECH_LANG.en;
        utterance.lang = langTag;
        const voice = pickVoice(langTag);
        if (voice) utterance.voice = voice;
        const clear = () => {
          // Only clear if we are still the active utterance.
          if (tokenRef.current === myToken) setActiveId(null);
        };
        utterance.onend = clear;
        utterance.onerror = clear;
        setActiveId(id);
        window.speechSynthesis.speak(utterance);
      } catch {
        // Reading aloud is best-effort; failures just leave us idle.
        if (tokenRef.current === myToken) setActiveId(null);
      }
    },
    [],
  );

  // Stop any in-flight speech if the provider unmounts (e.g. navigation away).
  useEffect(() => {
    return () => {
      if (hasSpeechSynthesis()) window.speechSynthesis.cancel();
    };
  }, []);

  const value = useMemo<ReadAloudController>(
    () => ({ activeId, speak, stop }),
    [activeId, speak, stop],
  );

  return (
    <ReadAloudContext.Provider value={value}>
      {children}
    </ReadAloudContext.Provider>
  );
}

/**
 * Access the shared controller. Falls back to a self-contained controller when
 * used OUTSIDE a provider, so a lone button (e.g. the Voice Q&A answer) still
 * works without wiring a provider around it.
 */
export function useReadAloud(): ReadAloudController {
  const shared = useContext(ReadAloudContext);
  const [localActive, setLocalActive] = useState<string | null>(null);
  const localTokenRef = useRef(0);

  const localStop = useCallback(() => {
    localTokenRef.current += 1;
    if (hasSpeechSynthesis()) window.speechSynthesis.cancel();
    setLocalActive(null);
  }, []);

  const localSpeak = useCallback(
    (id: string, text: string, language: SupportedLanguage) => {
      if (!hasSpeechSynthesis() || !text.trim()) return;
      localTokenRef.current += 1;
      const myToken = localTokenRef.current;
      try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        const langTag = SPEECH_LANG[language] ?? SPEECH_LANG.en;
        utterance.lang = langTag;
        const voice = pickVoice(langTag);
        if (voice) utterance.voice = voice;
        const clear = () => {
          if (localTokenRef.current === myToken) setLocalActive(null);
        };
        utterance.onend = clear;
        utterance.onerror = clear;
        setLocalActive(id);
        window.speechSynthesis.speak(utterance);
      } catch {
        if (localTokenRef.current === myToken) setLocalActive(null);
      }
    },
    [],
  );

  useEffect(() => {
    if (shared) return; // provider owns cleanup when present
    return () => {
      if (hasSpeechSynthesis()) window.speechSynthesis.cancel();
    };
  }, [shared]);

  const local = useMemo<ReadAloudController>(
    () => ({ activeId: localActive, speak: localSpeak, stop: localStop }),
    [localActive, localSpeak, localStop],
  );

  return shared ?? local;
}
